"""Check upgrade and restore on disposable copies of a schema-31 snapshot."""
import argparse
from contextlib import closing
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory

from dean.db import connect, migrate, schema_version


def copy(source, target):
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as active:
        with closing(connect(target)) as destination:
            active.backup(destination)


def snapshot(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        tables = {}
        for name, ddl in db.execute("SELECT name,sql FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            safe_name = '"' + name.replace('"', '""') + '"'
            rows = sorted(repr(row) for row in db.execute(f"SELECT * FROM {safe_name}"))
            tables[name] = (ddl, len(rows), sha256(repr(rows).encode()).hexdigest())
        return db.execute("PRAGMA user_version").fetchone()[0], tables


def baseline_code(reference, destination):
    data = subprocess.check_output(["git", "archive", "--format=tar", reference])
    with tarfile.open(fileobj=BytesIO(data)) as tar:
        tar.extractall(destination, filter="data")
    for line in (destination / "SNAPSHOT.sha256").read_text().splitlines():
        checksum, name = line.split("  ", 1)
        assert sha256((destination / name).read_bytes()).hexdigest() == checksum, name


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_ref")
    parser.add_argument("backup", type=Path)
    args = parser.parse_args()
    source = args.backup.resolve(strict=True)
    original_hash = sha256(source.read_bytes()).hexdigest()
    v31, before = snapshot(source)
    assert v31 == 31

    with TemporaryDirectory(prefix="dean-ext-rollback-") as temporary:
        folder = Path(temporary)
        upgraded = folder / "upgrade.sqlite3"
        copy(source, upgraded)
        with closing(connect(upgraded)) as db:
            migrate(db)
        first = snapshot(upgraded)
        with closing(connect(upgraded)) as db:
            migrate(db)
        second = snapshot(upgraded)
        version, updated = second
        assert first == second and version == schema_version()
        assert all(updated[name] == contents for name, contents in before.items())
        print(f"Upgrade 31 → {version} twice · {len(before)} original tables identical", flush=True)
        with closing(connect(upgraded)) as db:
            db.execute("""
                UPDATE bookable SET title='Rollback QA only'
                WHERE id=(SELECT id FROM bookable WHERE kind='activity' LIMIT 1)
            """)
        assert snapshot(upgraded) != second

        restored = folder / "restore.sqlite3"
        copy(source, restored)
        assert snapshot(restored) == (v31, before)
        baseline = folder / "app"
        baseline.mkdir()
        baseline_code(args.baseline_ref, baseline)
        probe = """
import sys
from pathlib import Path
from dean.access_config import AccessConfig
from dean.ai import RouterAI
from dean.config import WebConfig
from dean.db import schema_version
from dean.web import create_app
from tests.test_dean import Client
client = Client(create_app(Path(sys.argv[1]), WebConfig(), access=AccessConfig(), ai_engine=RouterAI()))
assert schema_version() == 31
for route in ("/healthz", "/login", "/guest"):
    assert client.request(route)[0] == 200, route
print("Original WSGI · /healthz, /login, /guest · 200")
"""
        subprocess.run([sys.executable, "-B", "-c", probe, str(restored)], cwd=baseline, check=True)
    assert sha256(source.read_bytes()).hexdigest() == original_hash
    print("Rollback · original manifest, schema, rows and backup hash verified")


if __name__ == "__main__":
    main()
