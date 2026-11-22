"""Create the deployment archive without runtime state or environment files."""

import argparse
import hashlib
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_ROOT = "DIGITAL_DEAN_PRODUCT"
SOURCE_DIRS = ("contracts", "dean", "deploy", "samples", "scripts", "tests")
IGNORED_DIRS = {"__pycache__", "archives", "lecture-packs", "node_modules"}
EXCLUDED_FILES = {"deploy/site.json"}
EXCLUDED_SUFFIXES = {".bundle", ".key", ".p12", ".pem", ".pfx", ".pyc", ".zip"}


def source_files():
    yield ROOT / ".dockerignore"
    yield ROOT / "requirements-auth.txt"
    yield from ROOT.glob("*.md")
    yield from ROOT.glob("*.command")
    for folder in SOURCE_DIRS:
        for directory, subdirs, names in os.walk(ROOT / folder):
            subdirs[:] = [name for name in subdirs if not name.startswith(".") and name not in IGNORED_DIRS]
            for name in names:
                path = Path(directory) / name
                relative = path.relative_to(ROOT).as_posix()
                if name.startswith(".") and relative != "deploy/.env.example":
                    continue
                if relative in EXCLUDED_FILES or path.suffix.lower() in EXCLUDED_SUFFIXES:
                    continue
                if ".sqlite" in name.lower() or name.lower().endswith(".accounts.json"):
                    continue
                yield path


def build(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".dean-release-", dir=output.parent) as temp:
        archive = Path(temp) / output.name
        with ZipFile(archive, "w") as bundle:
            for path in sorted(source_files()):
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError(f"Нельзя упаковать {path.relative_to(ROOT)}.")
                item = ZipInfo(ARCHIVE_ROOT + "/" + path.relative_to(ROOT).as_posix())
                item.create_system = 3
                item.external_attr = (stat.S_IFREG | (0o755 if mode & 0o111 else 0o644)) << 16
                bundle.writestr(item, path.read_bytes(), compress_type=ZIP_DEFLATED, compresslevel=7)
        with ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("Проверка CRC архива не прошла.")
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        archive.replace(output)
    print(f"{digest}  {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output.resolve())
