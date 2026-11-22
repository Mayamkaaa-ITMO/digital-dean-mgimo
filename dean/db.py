import os
import re
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[1]


def database_path():
    return Path(os.getenv("DEAN_DB", ROOT / ".runtime" / "dean.sqlite3")).resolve()


def connect(path, *, create=True):
    path = Path(path).resolve()
    if create:
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            pass
    db = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, isolation_level=None, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@contextmanager
def write(db):
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()


@contextmanager
def read_snapshot(db):
    db.execute("BEGIN")
    try:
        yield
    finally:
        db.rollback()


def migrations():
    files = sorted((ROOT / "dean" / "migrations").glob("*.sql"))
    numbers = [migration_version(file) for file in files]
    if numbers != list(range(1, len(files) + 1)):
        raise RuntimeError("Нарушена последовательность SQL-миграций.")
    return files


def migration_version(file):
    return int(file.name.split("_", 1)[0])


def schema_version():
    return migration_version(migrations()[-1])


def statements(file):
    statement = ""
    for line in file.read_text(encoding="utf-8").splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            yield statement.strip()
            statement = ""
    if statement.strip():
        raise RuntimeError(f"Неполная инструкция в миграции {file.name}")


def reconcile_branch_migration(db, file):
    # Product and design originally assigned v34/v35 to different SQL.
    # These four canonical files only create tables and indexes. Adopt an
    # existing object only when its definition matches the released DDL.
    for sql in statements(file):
        kind, name = re.match(r"CREATE (TABLE|INDEX) (\w+)\b", sql).groups()
        row = db.execute("SELECT type, sql FROM sqlite_schema WHERE name=?", (name,)).fetchone()
        if row is None:
            db.execute(sql)
        elif row[0] != kind.lower() or " ".join(row[1].split()) != " ".join(sql.rstrip(";").split()):
            raise RuntimeError(f"Несовместимое определение {name}. Обновление остановлено.")


def migrate(db):
    files = migrations()
    db.execute("PRAGMA journal_mode = WAL")
    with write(db):
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current > migration_version(files[-1]):
            raise RuntimeError("База создана более новой версией приложения.")
        for file in files:
            version = migration_version(file)
            if 34 <= version <= 37:
                reconcile_branch_migration(db, file)
            elif version > current:
                for sql in statements(file):
                    db.execute(sql)
            if version > current:
                db.execute(f"PRAGMA user_version = {version}")


def backup(source, destination):
    destination = destination.resolve()
    if destination == source.resolve() or destination.exists():
        raise FileExistsError("Укажи новый путь для резервной копии.")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with NamedTemporaryFile(dir=destination.parent, prefix=".backup-", delete=False) as file:
        staged = Path(file.name)
    try:
        with closing(connect(source, create=False)) as active, closing(connect(staged, create=False)) as copy:
            active.backup(copy)
            if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Проверка целостности резервной копии не прошла.")
        os.link(staged, destination)
    finally:
        staged.unlink(missing_ok=True)
