from contextlib import closing
from datetime import datetime

from dean import clock, staff_management
from dean.db import connect
from tests.test_dean import DemoFixture

TIME=datetime.fromisoformat("2026-11-18T09:00:00+00:00")
MANAGE="/staff/manage"
RESOURCES=MANAGE+"/resources/new"
SERVICE=MANAGE+"/services/new"


class StaffManagementTest(DemoFixture):
    def group(self):
        return self.db_one("SELECT group_id FROM account WHERE username='student-a'")

    def submit(self,client,page,action,**data):
        return client.request(action,{**client.fields(page,action),**data})

    def test_create_version_archive_service_with_old_submissions_retained(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        data={"group":self.group(),"title":"Демо-согласование","description":"Описание",
              "department":"Учебная часть А","category":"study","target_days":"4",
              "fields":"purpose | Цель обращения | textarea | 1","active":"1"}
        created=self.submit(staff,SERVICE,SERVICE,**data)
        self.assertEqual(created[0],303)
        admin=created[1]["Location"]
        sid=admin.rsplit("/",1)[1]
        form="/services/"+sid
        self.assertEqual(b.request(form)[0],404)
        stale=a.fields(form,form)
        entry=a.request(form,{**stale,"purpose":"Первоначальные данные"})
        self.assertEqual(entry[0],303)
        detail=entry[1]["Location"]
        self.assertIn("Цель обращения",a.request(detail)[2])
        fields={**data,"title":"Новая версия","fields":"hours | Часы | integer | 1"}
        self.assertEqual(self.submit(staff,admin,admin,**fields)[0],303)
        self.assertEqual(a.request(form,{**stale,"purpose":"Старая форма"})[0],409)
        self.assertIn("Цель обращения",a.request(detail)[2])
        self.assertIn("Демо-согласование",a.request(detail)[2])
        self.assertIn("Часы",a.request(form)[2])
        admin_fields=staff.fields(admin,admin)
        self.assertEqual(staff.request(admin,{**admin_fields,**fields,"active":"0"})[0],303)
        self.assertEqual(a.request(form)[0],404)
        change="/staff"+detail
        self.assertEqual(self.submit(staff,detail,change)[0],303)
        self.assertIn("Первоначальные данные",a.request(detail)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM managed_service_revision"),3)

    def test_event_published_after_draft_and_existing_registration_moves_and_cancels(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        with clock.at(TIME):
            args={"group":self.group(),"title":"Практикум новой команды","kind":"activity",
                  "place":"ДЕМО · корпус А","category":"Учёба","starts":"2026-11-20T16:30",
                  "ends":"2026-11-20T18:00","capacity":"2"}
            created=self.submit(staff,RESOURCES,RESOURCES,**args)
            self.assertEqual(created[0],303)
            resource=created[1]["Location"]
            sid=self.db_one("SELECT id FROM reservation_slot WHERE bookable_id=?",(resource.rsplit("/",1)[1],))
            detail="/slots/"+sid
            self.assertEqual(a.request(detail)[0],404)
            self.assertEqual(self.submit(staff,resource,resource+"/publish")[0],303)
            self.assertIn("/staff/slots/"+sid,staff.request(resource)[2])
            self.assertEqual(staff.request("/staff/slots/"+sid)[0],200)
            self.assertEqual(b.request(detail)[0],404)
            booked=self.submit(a,detail,detail+"/book",ack="1")
            self.assertEqual(booked[0],303)
            rid=booked[1]["Location"]
            self.assertEqual(self.submit(staff,resource,resource+"/move",starts="2026-11-21T17:00",
                                        ends="2026-11-21T18:45",capacity="2")[0],303)
            self.assertIn("17:00",a.request(rid)[2])
            self.assertIn("Мероприятие перенесено",a.request("/notifications")[2])
            self.assertEqual(self.submit(staff,resource,resource+"/retire")[0],303)
            self.assertEqual(a.request(detail)[0],404)
            self.assertIn("Мероприятие отменено",a.request("/notifications")[2])
            self.assertEqual(self.db_one("SELECT status FROM reservation WHERE id=?",(rid.rsplit("/",1)[1],)),"cancelled")

    def test_group_equipment_scope_and_scoped_instructor_assignment(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        csrf=staff.fields("/staff","/logout")["csrf"]
        self.assertEqual(staff.request(MANAGE+"/instructors",{"csrf":csrf})[0],403)
        with closing(connect(self.path)) as db:
            staff_management.manager_grant(db,"staff")
        group=self.group()
        teacher=self.db_one("SELECT id FROM account WHERE username='staff'")
        action=MANAGE+"/instructors"
        data=staff.fields(MANAGE,action)
        self.assertEqual(staff.request(action,{**data,"assignment":f"{teacher}|absent|{self.period}|physics"})[0],403)
        self.assertEqual(staff.request(action,{**data,"assignment":f"{teacher}|{group}|{self.period}|algorithms"})[0],303)
        self.assertIn("Алгоритмы",staff.request("/staff/work/new")[2])
        created=self.submit(staff,RESOURCES,RESOURCES,group=group,kind="equipment",title="Ноутбук группы",
                            place="ДЕМО · окно",return_place="ДЕМО · возврат",label="GR-02")
        self.assertEqual(created[0],303)
        uid=self.db_one("SELECT unit_id FROM managed_equipment")
        path="/equipment/"+uid
        self.assertEqual(a.request(path)[0],200)
        self.assertEqual(b.request(path)[0],404)
