from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import re

from dean import clock, reservations
from tests.test_dean import DemoFixture

TIME=datetime.fromisoformat("2026-11-19T09:00:00+00:00")
EVENT=datetime.fromisoformat("2026-11-20T13:00:00+00:00")
NEW="/staff/manage/resources/new"


class EventTicketTest(DemoFixture):
    def registration(self, a, staff):
        with clock.at(TIME):
            group=self.db_one("SELECT group_id FROM account WHERE username='student-a'")
            fields=staff.fields(NEW,NEW)
            result=staff.request(NEW,{**fields,"kind":"activity","group":group,"title":"Демо событие QR",
                                       "place":"ДЕМО · зал","category":"Учёба","capacity":"4",
                                       "starts":"2026-11-20T16:00","ends":"2026-11-20T17:00"})
            self.assertEqual(result[0],303)
            resource=result[1]["Location"]
            publish=resource+"/publish"
            self.assertEqual(staff.request(publish,staff.fields(resource,publish))[0],303)
            sid=self.db_one("SELECT id FROM reservation_slot WHERE bookable_id=?",(resource.rsplit("/",1)[1],))
            path="/slots/"+sid
            result=a.request(path+"/book",{**a.fields(path,path+"/book"),"ack":"1"})
            self.assertEqual(result[0],303)
            return sid,result[1]["Location"]

    def issue(self, a, rid):
        issue=rid+"/ticket"
        status,headers,body=a.request(issue,a.fields(rid,issue))
        self.assertEqual(status,200)
        self.assertIn("data:image/png;base64,",body)
        self.assertIn("img-src",headers["Content-Security-Policy"])
        return re.search(r'value="(DEAN1-[A-Za-z0-9_-]{43})"',body)[1]

    def test_ticket_reissue_confirmation_race_award_and_correction(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        sid,booking=self.registration(a,staff)
        slot="/staff/slots/"+sid
        check=slot+"/tickets"
        with clock.at(TIME):
            old=self.issue(a,booking)
            new=self.issue(a,booking)
            self.assertNotEqual(old,new)
            self.assertNotIn(new,a.request(booking)[2])
            self.assertEqual(staff.request(check,{**staff.fields(check,check),"code":new})[0],404)
        self.assertEqual(b.request(check)[0],403)
        with clock.at(EVENT):
            def scan(code):
                return staff.request(check,{**staff.fields(check,check),"code":code})
            self.assertEqual(scan(old)[0],404)
            wrong="/staff/slots/"+reservations.fixture_id("spbstu-demo","event-talk:0")+"/tickets"
            self.assertEqual(staff.request(wrong,{**staff.fields(wrong,wrong),"code":new})[0],404)
            first,second=(scan(new)[1]["Location"] for _ in range(2))
            self.assertEqual(staff.request(first)[0],200)
            self.assertEqual(self.db_one("SELECT count(*) FROM reservation_attendance"),0)
            actions=[(path,staff.fields(path,path)) for path in (first,second)]
        def verify(data):
            with clock.at(EVENT):
                return staff.request(*data)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(verify,actions)),[303,303])
        self.assertEqual(self.db_one("SELECT count(*) FROM reservation_attendance"),1)
        self.assertEqual(self.db_one("SELECT count(*) FROM portfolio_verification WHERE reservation_id IS NOT NULL"),1)
        self.assertIn("Посещение подтверждено",a.request(booking)[2])
        rid=booking.rsplit("/",1)[1]
        change=f"{slot}/attendance/{rid}/correct"
        with clock.at(EVENT+timedelta(minutes=2)):
            self.assertEqual(staff.request(change,{**staff.fields(slot,change),"reason":"Ошибка проверки"})[0],303)
            self.assertEqual(scan(new)[0],404)
        self.assertEqual(self.db_one("SELECT count(*) FROM portfolio_verification WHERE reservation_id IS NOT NULL"),0)
        self.assertEqual(self.db_one("SELECT count(*) FROM event_ticket_history WHERE action='confirmed'"),1)
        self.assertEqual(self.db_one("SELECT count(*) FROM event_ticket_history WHERE action='corrected'"),1)

    def test_cancelled_booking_and_rate_limit(self):
        a,staff=self.client("student-a"),self.client("staff")
        sid,booking=self.registration(a,staff)
        check="/staff/slots/"+sid+"/tickets"
        with clock.at(TIME):
            code=self.issue(a,booking)
            cancel=booking+"/cancel"
            self.assertEqual(a.request(cancel,a.fields(booking,cancel))[0],303)
        with clock.at(EVENT):
            fields=staff.fields(check,check)
            for _ in range(12):
                self.assertEqual(staff.request(check,{**fields,"code":code})[0],404)
            self.assertEqual(staff.request(check,{**fields,"code":code})[0],429)
        self.assertEqual(self.db_one("SELECT count(*) FROM reservation_attendance"),0)
