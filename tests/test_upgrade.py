from contextlib import closing
from datetime import datetime
from pathlib import Path

from dean.clock import at
from dean.db import ROOT, backup, connect, migrate, schema_version
from dean.seed import seed, seed_id
from dean.web import create_app

from .test_dean import Client, DemoFixture


class UpgradeTest(DemoFixture):
    def test_original_audit_database_survives_upgrade_and_extensions_do_not_reset_work(self):
        old_path = Path(self.workspace.name) / "original.sqlite3"
        with closing(connect(old_path)) as db:
            original = (ROOT/"dean/migrations/001_initial.sql").read_text()
            original += (ROOT/"dean/migrations/002_login_session_indexes.sql").read_text()
            db.executescript(original + "\nPRAGMA user_version=2;")
            seed(db,old_path,self.today,self.passwords)
            from uuid import uuid4
            aid=uuid4().hex
            stamp="2026-11-17T09:00:00+00:00"
            db.execute("""
                INSERT INTO application VALUES (?, ?, ?, ?, 'consultation', ?, 'review', ?, ?)
            """, (aid,"spbstu-demo",seed_id("student-a"),"demo-ai","Заявка до обновления.",stamp,stamp))
            db.executemany("""
                INSERT INTO application_history (application_id,actor_id,status,note,created_at)
                VALUES (?, ?, ?, ?, ?)
            """, [(aid,seed_id("student-a"),"submitted","",stamp),
                  (aid,seed_id("staff"),"review","Старая история.",stamp)])
        data={"passwords":self.passwords,"aid":aid}
        with closing(connect(old_path)) as db:
            hashes=list(map(tuple,db.execute("SELECT id,password_hash FROM account ORDER BY id")))
            before=list(map(tuple,db.execute("SELECT id,kind,target_id FROM notification ORDER BY id")))
            aid=data["aid"]
        snapshot = Path(self.workspace.name) / "before.sqlite3"
        backup(old_path,snapshot)
        with closing(connect(old_path)) as db:
            migrate(db)
            seed(db,old_path,self.today)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],schema_version())
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(),[])
            self.assertEqual(list(map(tuple,db.execute("SELECT id,password_hash FROM account ORDER BY id"))),hashes)
            self.assertEqual(list(map(tuple,db.execute("SELECT id,kind,target_id FROM notification ORDER BY id"))),before)
            services = {row[0] for row in db.execute("SELECT id FROM service")}
            self.assertTrue({"consultation", "data-question", "enrolment-certificate", "academic-transcript"} <= services)
            self.assertEqual(db.execute("SELECT count(*) FROM application_history WHERE application_id=?", (aid,)).fetchone()[0],2)
            db.execute("UPDATE service SET description='Редактировано' WHERE id='enrolment-certificate'")
            seed(db,old_path,self.today)
            self.assertEqual(db.execute("SELECT description FROM service WHERE id='enrolment-certificate'").fetchone()[0],"Редактировано")
        a=Client(create_app(old_path))
        self.assertEqual(a.login("student-a",data["passwords"]["student-a"])[0],303)
        self.assertIn("Старая история.",a.request(f"/requests/{aid}")[2])
        self.assertIn("Заявка до обновления.",a.request(f"/requests/{aid}")[2])

    def test_malformed_unknown_score_and_day_boundaries(self):
        a=self.client("student-a")
        previous=self.db_one("SELECT period_id FROM account WHERE username='student-a'")
        rid=seed_id(f"student-a:databases:{previous}")
        page=f"/grades/{rid}"
        parts=a.fields(page,page+"/scenario")
        self.assertEqual(a.request(page+"/scenario",{**parts,"target":"NaN","exam_points":"0"})[0],422)
        from zoneinfo import ZoneInfo
        zone=ZoneInfo("Europe/Moscow")
        with at(datetime.combine(self.today,datetime.min.time(),zone)):
            html=a.request("/")[2]
            self.assertIn("Ближайшая пара",html)
            self.assertNotIn("Все пары сегодня",html)
        with at(datetime.combine(self.today,datetime.min.time(),zone).replace(hour=11,minute=30)):
            html=a.request("/")[2]
            self.assertNotIn("Идёт сейчас",html)
            self.assertIn("Ближайшая пара",html)
