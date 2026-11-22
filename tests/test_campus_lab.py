import json
from contextlib import closing
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from uuid import uuid4

from dean import campus_lab, coursework, room_occupancy
from dean.db import connect
from dean.static_assets import asset_url
from tests.test_assisted_dean import PIXEL, upload
from tests.test_dean import Client, DemoFixture

FLAGS={"DEAN_CAMPUS_LAB":"1","DEAN_EXT_ROOM_OCCUPANCY":"1","DEAN_EXT_CAMPUS_PAY":"1","DEAN_EXT_CAMPUS_DIRECTORY":"1"}


class CampusLabTest(DemoFixture):
    def client(self, login):
        client=Client(self.app)
        self.assertEqual(client.login(login,self.passwords[login])[1]["Location"],campus_lab.PATH)
        return client

    def setUp(self):
        super().setUp()
        flags=patch.dict("os.environ", FLAGS); flags.start(); self.addCleanup(flags.stop)
        self.staff,self.a,self.b=(self.client(r) for r in ("staff","student-a","student-b"))
        with closing(connect(self.path)) as db:
            coursework.grant_demo(db,"staff","demo-ai","databases")
            self.scope="|".join(db.execute("SELECT group_id,period_id,subject_id FROM instructor_scope LIMIT 1").fetchone())

    def test_lecture_live_access_archive_and_homework(self):
        new=self.staff.fields("/lectures","/lectures")
        created=self.staff.request("/lectures",{**new,"scope":self.scope,"title":"Риторика"})
        self.assertEqual(created[0],303)
        url=created[1]["Location"]
        self.assertIn(url,self.a.request("/lectures")[2])
        for client in (self.staff, self.a):
            html = client.request(url)[2]
            self.assertIn(asset_url("campus-lab.css"), html)
            self.assertIn(asset_url("campus-lab.js"), html)
        self.assertEqual(self.b.request(url)[0],404)
        form=self.staff.fields(url,url+"/sample")
        self.assertEqual(self.staff.request(url+"/sample",form)[0],303)
        self.assertEqual(self.staff.request(url+"/sample",form)[0],303)
        self.assertEqual(json.loads(self.a.request(url+"/manifest")[2]),{"count":1,"live":True})
        self.assertEqual(self.b.request(url+"/segments/0")[0],404)
        self.assertEqual(self.a.request(url+"/sample",{**form,"csrf":self.a.fields("/campus-directory","/campus-lab/presence")["csrf"]})[0],404)
        self.assertEqual(self.staff.request(url+"/end",self.staff.fields(url,url+"/end"))[0],303)
        self.assertFalse(json.loads(self.a.request(url+"/manifest")[2])["live"])
        text="Первичный ключ определяет запись. Индекс ускоряет поиск строк."
        with patch("dean.lecture_analysis.transcribe",return_value=(text,"тестовый распознаватель")):
            self.assertEqual(self.staff.request(url+"/analyse",self.staff.fields(url,url+"/analyse"))[0],303)
        self.assertIn(text,self.a.request(url)[2])
        work=self.staff.fields(url,url+"/assignment")
        result=self.staff.request(url+"/assignment",{**work,"title":"Разобрать запись","description":"Сравни ключ и индекс","rubric":"Точность | 5"})
        self.assertEqual(result[0],303)
        card=result[1]["Location"]
        self.assertEqual(self.staff.request(card+"/publish",self.staff.fields(card,card+"/publish"))[0],303)
        self.assertIn("Разобрать запись",self.a.request("/work")[2])

    def test_nfc_presence_and_door(self):
        url="/campus-pay"
        data=self.a.fields(url,url)
        card=self.a.request(url,{**data,"product":"tea"})[1]["Location"]
        for client, page in ((self.a, card), (self.a, "/campus-directory"),
                             (self.staff, "/campus-lab/terminal"), (self.a, "/campus-occupancy")):
            with self.subTest(page=page):
                html = client.request(page)[2]
                self.assertIn(asset_url("campus-lab.css"), html)
                self.assertIn(asset_url("campus-lab.js"), html)
        f=self.a.fields(card,card+"/tap");f.update(terminal=campus_lab.TAG,mode="nfc")
        self.assertEqual(self.a.request(card+"/tap",{**f,"terminal":"unknown"})[0],422)
        self.assertEqual(self.a.request(card+"/tap",f)[0],303)
        self.assertEqual(self.a.request(card+"/tap",f)[0],303)
        self.assertIn("265 демо-баллов",self.a.request(url)[2])
        self.assertIn("Способ подтверждения · NFC-метка",self.a.request(card)[2])
        publish="/campus-lab/presence"
        geo=self.a.fields("/campus-directory",publish)
        self.assertEqual(self.a.request(publish,{**geo,"state":"near"})[0],303)
        self.assertIn("В кампусе",self.staff.request("/campus-directory")[2])
        with closing(connect(self.path)) as db:
            db.execute("UPDATE lab_presence SET expires='2000-01-01'")
        self.assertNotIn(" · В кампусе",self.staff.request("/campus-directory")[2])
        url="/campus-occupancy"
        self.assertEqual(self.staff.request(url,self.staff.fields(url,url))[0],303)
        self.assertNotIn("Камера у двери", self.staff.request(url)[2])
        with patch.dict("os.environ", {"DEAN_ROOM_OCCUPANCY_CAMERA": "1"}):
            self.assertIn("Камера у двери", self.staff.request(url)[2])
            self.assertNotIn("Камера у двери", self.a.request(url)[2])
        fields=self.staff.fields(url,url+"/door")
        fields.update(room="b1-1012",direction="enter")
        self.assertEqual(self.staff.request(url+"/door",fields)[0],303)
        self.assertEqual(self.staff.request(url+"/door",fields)[0],303)
        self.assertIn("Сигнал",self.a.request(url)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM lab_door"),1)
        forbidden={**geo,"room":"b1-1012","direction":"enter","request_id":uuid4().hex}
        self.assertEqual(self.a.request(url+"/door",forbidden)[0],403)

    def test_forecast_learns_from_observations(self):
        t=datetime(2026,11,17,10,tzinfo=timezone.utc)
        def sample(target):
            return [(t+timedelta(days=d,hours=h),n) for d in range(10) for h,n in ((0,8),(1,target))]
        self.assertEqual(room_occupancy.nearest_hour(sample(10),t+timedelta(days=11),8,32),10)
        self.assertEqual(room_occupancy.nearest_hour(sample(18),t+timedelta(days=11),8,32),18)

    def test_campus_assistant_uses_real_role_data_and_events_desk(self):
        from dean import ai, assistant
        url="/campus-lab/events"
        self.assertEqual(self.staff.request(url)[0],200)
        self.assertIn("Создать мероприятие",self.staff.request(url)[2])
        self.assertEqual(self.a.request(url)[0],403)
        with closing(connect(self.path)) as db:
            student=db.execute("SELECT * FROM account WHERE username='student-a'").fetchone()
            staff=db.execute("SELECT * FROM account WHERE username='staff'").fetchone()
            result=assistant.ask(db,student,ai.RouterAI(),"Какой у меня баланс в кампусе?","",uuid4().hex)
            self.assertIn("300",result["text"])
            self.assertEqual(result["link"],"/campus-pay")
            self.assertNotIn("wallet",assistant.readable_sections(staff))
            values=assistant.ask(db,staff,ai.RouterAI(),"Где свободные места в аудитории?","",uuid4().hex)
            self.assertIn("1012",values["text"])

    def test_processing_error_can_be_retried(self):
        from dean.domain import DomainError
        data=self.staff.fields("/lectures","/lectures")
        url=self.staff.request("/lectures",{**data,"scope":self.scope,"title":"Повтор обработки"})[1]["Location"]
        for action in ("sample","end"):
            self.staff.request(url+"/"+action,self.staff.fields(url,url+"/"+action))
        with patch("dean.lecture_analysis.transcribe",side_effect=DomainError(503,"Модель недоступна")):
            self.staff.request(url+"/analyse",self.staff.fields(url,url+"/analyse"))
        self.assertIn("Модель недоступна",self.staff.request(url)[2])
        self.assertEqual(self.b.request(url+"/status")[0],404)
        with patch("dean.lecture_analysis.transcribe",return_value=("Фраза из записи.","Проверка")):
            self.staff.request(url+"/analyse",self.staff.fields(url,url+"/analyse"))
        self.assertTrue(json.loads(self.staff.request(url+"/status")[2])["ready"])
        self.assertIn("Фраза из записи",self.a.request(url)[2])

    def test_local_photo_export_keeps_verified_room_and_scope(self):
        recognized={"events":[{"day":1,"start":"09:00","end":"10:30","subject":"Картография",
                                "kind":"семинар","room":"1012","uncertain":True},
                               {"day":3,"start":"14:00","end":"15:30","subject":"Оптика",
                                "kind":"практика","room":"2018а","uncertain":True}]}
        with patch.dict("os.environ",{"DEAN_EXT_PHOTO_IMPORT":"1"}), patch(
            "dean.campus_photo.local", return_value=(recognized, "Локальное распознавание"),
        ):
            url="/calendar/import/photo"
            data=self.a.fields(url,url)
            data.update(week="2026-11-23")
            result=upload(self.a,url,data,PIXEL)
            self.assertEqual(result[0],303)
            draft=result[1]["Location"]
            export=draft+"/calendar.ics"
            self.assertEqual(self.a.request(export)[0],404)
            for line in ("1","2"):
                from tests.test_dean import Forms
                import re
                html=self.a.request(draft)[2]
                forms=[Forms(match[0]).forms for match in re.finditer(r"<form\b.*?</form>",html,re.S)]
                form=next(group[draft+"/row"] for group in forms if group.get(draft+"/row",{}).get("line")==line)
                form.update(reviewed="1",room="А-555" if line=="1" else "2018а")
                self.assertEqual(self.a.request(draft+"/row",form)[0],303)
            self.assertEqual(self.a.request(draft+"/confirm",self.a.fields(draft,draft+"/confirm"))[0],303)
            status,headers,body=self.a.request(export)
            self.assertEqual(status,200)
            self.assertIn("text/calendar",headers["Content-Type"])
            self.assertEqual(body.count("BEGIN:VEVENT"),2)
            self.assertIn("LOCATION:А-555",body)
            self.assertIn("DTSTART:20261123T060000Z",body)
            self.assertEqual(self.b.request(export)[0],404)
