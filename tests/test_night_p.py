from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta

from dean import clock, jobs, public_schedule, public_sync, reminders
from dean.db import connect, write
from dean.domain import DomainError, timestamp
from tests.test_dean import DemoFixture
from tests.test_night_l import FIRST, START, choose

TIME = datetime.fromisoformat("2026-11-18T01:05:00+00:00")


class BackgroundTest(DemoFixture):
    def test_worker_recovers_lease_and_deduplicates_concurrent_runs(self):
        with clock.at(TIME), closing(connect(self.path)) as db:
            jobs.enqueue(db,TIME)
            lost=jobs.claim(db,TIME)
        def worker(when):
            with clock.at(when), closing(connect(self.path)) as db:
                return jobs.run_once(db)
        with ThreadPoolExecutor(max_workers=2) as executor:
            first=list(executor.map(worker,(TIME,TIME)))
        self.assertEqual(sum(result["completed"] for result in first),1)
        self.assertEqual(worker(TIME+jobs.LEASE)["completed"],2)
        with closing(connect(self.path)) as db:
            self.assertFalse(jobs.perform(db,lost,TIME+jobs.LEASE,reminders.refresh_in_transaction))
            self.assertEqual(jobs.diagnostics(db)["reminders"]["leased"],0)
        with self.assertRaises(DomainError) as error:
            worker(TIME)
        self.assertEqual(error.exception.status,409)

    def test_failure_backoff_isolated_and_rolled_back(self):
        with closing(connect(self.path)) as db:
            victim=db.execute("SELECT id FROM account WHERE username='student-a'").fetchone()[0]
            def fault(db,user,current):
                if user["id"]==victim:
                    db.execute("UPDATE account SET full_name='must rollback' WHERE id=?",(victim,))
                    raise ValueError("private details to omit")
                return reminders.refresh_in_transaction(db,user,current)
            for step in range(jobs.MAX_FAILURES):
                with clock.at(TIME+timedelta(minutes=40*step)):
                    counts=jobs.run_once(db,refresh=fault)
                    self.assertEqual((counts["completed"],counts["failed"]),(1,1))
            with clock.at(TIME+timedelta(hours=5)):
                self.assertEqual(jobs.run_once(db,refresh=fault)["failed"],0)
                status=jobs.diagnostics(db)
                self.assertEqual(status["reminders"]["exhausted"],1)
                self.assertEqual(status["reminders"]["last_error"],"refresh")
                self.assertNotIn("private details",str(status))
                self.assertEqual(jobs.retry(db),1)
                self.assertEqual(jobs.run_once(db)["completed"],1)
            row=db.execute("SELECT full_name FROM account WHERE id=?",(victim,)).fetchone()
            self.assertNotEqual(row[0],"must rollback")

    def test_audited_rollback_preserves_notes_and_revokes_only_new_event(self):
        a, staff=self.client("student-a"), self.client("staff")
        week=public_schedule.week_scope(*FIRST.split("|"),START)
        events=public_schedule.sample_events(week)
        with closing(connect(self.path)) as db:
            public_sync.allow_operator(db,"staff")
            initial=public_sync.stage_sample(db,week)
            public_sync.publish(db,initial)
            newer=replace(events[0],external_id="temporary-extra")
            with write(db):
                preview=public_sync.stage_in_transaction(db,week,(*events,newer),"synthetic","2026-11-18")
            public_sync.publish(db,preview)
        choose(a,FIRST)
        detail="/schedule/public/"+public_schedule.lesson_id(week,newer.external_id)
        note=detail+"/note"
        self.assertEqual(a.request(note,{**a.fields(detail,note),"body":"Сохрани после отката"})[0],303)
        restore=f"/staff/public/{initial}"
        rollback=restore+"/rollback"
        fields=staff.fields(restore,rollback)
        own_csrf=a.fields("/schedule/context","/schedule/context")["csrf"]
        self.assertEqual(a.request(rollback,{**fields,"csrf":own_csrf})[0],403)
        staged=staff.request(rollback,fields)
        self.assertEqual(staged[0],303)
        comparison=staged[1]["Location"]
        self.assertIn("Откат к",staff.request(comparison)[2])
        confirm=staff.fields(comparison,comparison)
        self.assertEqual(staff.request(comparison,confirm)[0],303)
        self.assertIn("Отменено источником",a.request(detail)[2])
        self.assertIn("Сохрани после отката",a.request(detail)[2])
        self.assertEqual(self.db_one("SELECT revision FROM public_week"),3)
        self.assertIsNotNone(self.db_one("SELECT approved_by FROM public_rollback"))
        self.assertEqual(self.db_one("SELECT provenance FROM public_lesson_change ORDER BY rowid DESC LIMIT 1"),"rollback")
