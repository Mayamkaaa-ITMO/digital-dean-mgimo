"""Recover same-number design/product schemas and refuse partial incompatible upgrades."""
import sqlite3
from contextlib import closing
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from dean.db import connect, migrate, migrations, migration_version, schema_version, statements


class FinalMigrationTest(TestCase):
    def legacy(self, files, version, path):
        db = connect(path)
        for file in migrations():
            if migration_version(file) <= 33 or migration_version(file) in files:
                for sql in statements(file):
                    db.execute(sql)
        db.execute(f"PRAGMA user_version = {version}")
        return db

    def test_historical_branches_converge_without_data_loss(self):
        with TemporaryDirectory() as tmp:
            for name, files, version in (
                ("design34", {34}, 34), ("design35", {34, 35}, 35),
                ("product34", {36}, 34), ("product35", {36, 37}, 35),
                ("partial35", {34, 37}, 35), ("mixed37", {35, 36, 37}, 37),
            ):
                with self.subTest(name=name):
                    db = self.legacy(files, version, Path(tmp)/name)
                    try:
                        if 37 in files:
                            db.execute("INSERT INTO demo_room_flow VALUES ('sample','2030-01-01',10,2)")
                        for _ in range(2):
                            migrate(db)
                        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], schema_version())
                        for table in ("assistant_conversation","assistant_message","profile_photo","campus_demo_purchase","demo_room_flow"):
                            self.assertIsNotNone(db.execute("SELECT sql FROM sqlite_schema WHERE name=?", (table,)).fetchone())
                        if 37 in files:
                            self.assertEqual(tuple(db.execute("SELECT entered,exited FROM demo_room_flow").fetchone()),(10,2))
                        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                        self.assertFalse(db.execute("PRAGMA foreign_key_check").fetchall())
                    finally:
                        db.close()

    def test_legacy_room_signals_keep_the_demo_tenant(self):
        with TemporaryDirectory() as tmp:
            with closing(self.legacy(set(range(34, 48)), 47, Path(tmp)/"occupancy")) as db:
                db.execute("INSERT INTO lab_door(room,recorded,delta,occupied) VALUES ('b1-1012','2030-01-01',1,9)")
                migrate(db)
                row = db.execute("SELECT university_id,room,occupied FROM lab_door").fetchone()
                self.assertEqual(tuple(row), ("spbstu-demo", "b1-1012", 9))

    def test_unknown_definition_fails_atomically(self):
        with TemporaryDirectory() as tmp:
            db = self.legacy({36}, 34, Path(tmp)/"unknown")
            try:
                db.execute("CREATE TABLE profile_photo (unrelated TEXT)")
                with self.assertRaisesRegex(RuntimeError, "profile_photo"):
                    migrate(db)
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 34)
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_schema WHERE name='assistant_message'").fetchone())
            finally:
                db.close()
