from contextlib import closing
from unittest.mock import Mock

from dean import public_probe, public_schedule, public_subjects, public_sync, public_transport
from dean.db import connect
from tests.test_dean import DemoFixture
from tests.test_night_l import choose

SCOPE=("tusur-timetable","fvs/516-1")
START="2026-11-16"


class PublicDepthTest(DemoFixture):
    def test_scoped_discipline_to_room_services_and_private_subject(self):
        with closing(connect(self.path)) as db:
            week=public_schedule.week_scope(*SCOPE,START)
            event=next(item for item in public_schedule.sample_events(week) if item.subject=="Математика")
            key=public_sync.stage_sample(db,week)
            self.assertGreater(public_sync.publish(db,key)["added"],0)
        a,b=self.client("student-a"),self.client("student-b")
        choose(a,"|".join(SCOPE))
        choose(b,"|".join(SCOPE))
        lecture="/schedule/public/"+public_schedule.lesson_id(week,event.external_id)
        detail=public_subjects.subject_href(SCOPE,event.subject)
        self.assertIn(detail,a.request(lecture)[2])
        self.assertIn("Официальная неделя",a.request(detail)[2])
        self.assertIn("Опубликованная сетка звонков",a.request("/study/public/services/calendar")[2])
        library=a.request("/study/public/services/library")[2]
        self.assertIn("Красноармейская",library)
        response=a.request("/study/subjects",{**a.fields(detail,"/study/subjects"),"source_title":event.subject})
        self.assertEqual(response[0],303)
        personal=response[1]["Location"]
        self.assertEqual(b.request(personal)[0],404)
        form=personal+"/save"
        self.assertEqual(a.request(form,{**a.fields(personal,form),"title":"Математика","note":"Приватно"} )[0],303)
        with closing(connect(self.path)) as db:
            self.assertEqual(public_sync.publish(db,public_sync.stage_sample(db,week))["added"],0)
        self.assertIn("Приватно",self.read_after_restart(a,personal))
        choose(a,"tusur-timetable|fvs/526-1")
        self.assertEqual(a.request(detail)[0],404)
        self.assertNotEqual(detail,public_subjects.subject_href(("tusur-timetable","fvs/526-1"),event.subject))

    def test_live_probe_failure_and_unsupported_adapter_never_publish(self):
        failed=Mock(side_effect=public_transport.FetchError("dns"))
        status=public_probe.run(*SCOPE,START,read=failed)
        self.assertEqual(status["result"],"dns")
        self.assertFalse(status["publication"])
        self.assertFalse(status["live_refresh_enabled"])
        self.assertEqual(public_probe.run("spbpu-ruz","125/45492",START,read=failed)["result"],
                         "adapter_unavailable")
        self.assertEqual(failed.call_count,1)
        self.assertEqual(self.db_one("SELECT count(*) FROM public_week"),0)
