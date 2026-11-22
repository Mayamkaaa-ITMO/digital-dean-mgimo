from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from dean import clock
from tests.test_dean import DemoFixture
from tests.test_night_l import FIRST, choose

PREF="/profile/dashboard"
TIME=datetime.fromisoformat("2026-11-18T12:00:00+00:00")


class OptionalScenariosTest(DemoFixture):
    def test_dashboard_order_hidden_panels_and_context_isolation(self):
        a,b=self.client("student-a"),self.client("student-b")
        fields=a.fields(PREF,PREF)
        fields.update(panel_tasks="1",panel_exam="3",shortcut_task="1",shortcut_certificate="2")
        self.assertEqual(a.request(PREF,fields)[0],303)
        self.assertEqual(a.request(PREF,fields)[0],409)
        zone = ZoneInfo("Europe/Moscow")
        with clock.at(datetime.combine(self.today, datetime.min.time(), zone)):
            form = "/tasks/new"
            task = a.request(form, {**a.fields(form, form), "title": "Приоритетная задача", "due": f"{self.today}T00:30"})
            self.assertEqual(task[0], 303)
            body = a.request("/")[2]
        self.assertLess(body.index("Приоритетная задача"), body.index("Экзамен · "))
        self.assertNotIn("Дела и обновления",body)
        self.assertNotIn("Все пары сегодня", body)
        self.assertNotIn("<h2>Занятия", body)
        restored = self.read_after_restart(a, "/")
        self.assertLess(restored.index('href="/tasks/new"'),restored.index('href="/services/enrolment-certificate"'))
        self.assertNotIn('href="/tasks/new"',b.request("/")[2])
        choose(a,FIRST)
        self.assertIn("Источник расписания",a.request("/schedule")[2])
        self.assertNotIn("Ближайший экзамен",a.request("/")[2])
        choose(a,"")
        fields=a.fields(PREF,PREF)
        self.assertEqual(a.request(PREF,{**fields,"reset":"1"})[0],303)
        self.assertIn('href="/requests?mode=catalog"',a.request("/")[2])

    def issue(self,a,staff):
        form="/services/enrolment-certificate"
        response=a.request(form,{**a.fields(form,form),"purpose":"Проверка передачи"})
        self.assertEqual(response[0],303)
        detail=response[1]["Location"]
        status="/staff"+detail
        for _ in range(2):
            self.assertEqual(staff.request(status,staff.fields(detail,status))[0],303)
        return self.db_one("SELECT id FROM document")

    def test_document_link_authorization_access_one_pdf_expiry_and_revocation(self):
        a,b,staff,reader=self.client("student-a"),self.client("student-b"),self.client("staff"),self.client()
        did=self.issue(a,staff)
        pdf="/documents/"+did
        share=pdf+"/share"
        self.assertEqual(b.request(share)[0],404)
        self.assertEqual(staff.request(share)[0],403)
        self.assertIn(share,a.request("/documents")[2])
        fields=a.fields(share,share)
        self.assertEqual(a.request(share,{**fields,"duration":"1"})[0],422)
        with clock.at(TIME):
            created=a.request(share,{**fields,"duration":"1","confirm":"1"})
            self.assertEqual(created[0],200)
            target=re.search(r"(/shared/document/[A-Za-z0-9_-]{43})",created[2])[1]
            results=reader.request(target)
            self.assertEqual(results[0],200)
            self.assertEqual(results[2],a.request(pdf)[2])
            self.assertEqual(reader.request(pdf)[0],303)
            self.assertNotIn(target,a.request(share)[2])
            key=self.db_one("SELECT id FROM document_share")
            revoke=share+"/"+key+"/revoke"
            csrf=b.fields("/profile","/logout")["csrf"]
            self.assertEqual(b.request(revoke,{"csrf":csrf})[0],404)
            with clock.at(TIME+timedelta(hours=1)):
                self.assertEqual(reader.request(target)[0],404)
            self.assertEqual(a.request(revoke,a.fields(share,revoke))[0],303)
            self.assertEqual(reader.request(target)[0],404)
            self.assertEqual(reader.request(target,{})[0],405)
