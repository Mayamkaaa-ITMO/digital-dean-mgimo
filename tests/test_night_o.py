import json
from html import unescape
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from tests.test_dean import DemoFixture

INDEX="/calendar/import"
EXPORT="/calendar/export"
RANGE="?first=2026-11-16&last=2026-11-28&scope=personal&download=1"
TEMPLATE="key,subject,kind,start,end,room,status\n"


def parse_ics(text):
    """Independent fixture reader of the supported VCALENDAR/VEVENT subset."""
    physical = text.split("\r\n")
    assert physical[-1] == "" and all(len(row.encode("utf-8"))<=75 for row in physical)
    unfolded=[]
    for row in physical[:-1]:
        if row[:1] in {" ","\t"}:
            unfolded[-1]+=row[1:]
        else:
            unfolded.append(row)
    assert unfolded[0]=="BEGIN:VCALENDAR" and unfolded[-1]=="END:VCALENDAR"
    items={}
    event=None
    for field in unfolded[1:-1]:
        name,value=field.split(":",1)
        if field=="BEGIN:VEVENT":
            assert event is None
            event={}
        elif field=="END:VEVENT":
            assert event and "UID" in event and ("DTSTART" in event or "DTSTART;VALUE=DATE" in event) and event["UID"] not in items
            items[event["UID"]]=event
            event=None
        elif event is not None:
            event[name]=value
    assert event is None
    return items


class CalendarPortabilityTest(DemoFixture):
    def make_draft(self, a, format_name, content):
        path=INDEX+"/"+format_name
        fields=a.fields(INDEX,path)
        response=a.request(path,{**fields,"data":content})
        self.assertEqual(response[0],303)
        return response[1]["Location"]

    def confirm(self,a,detail):
        route=detail+"/confirm"
        form=a.fields(detail,route)
        self.assertEqual(a.request(route,form)[0],303)
        self.assertEqual(a.request(route,form)[0],303)

    def test_csv_preview_idempotent_move_and_explicit_cancellation_have_stable_ids(self):
        a,b=self.client("student-a"),self.client("student-b")
        s=TEMPLATE+"sample,Календарный предмет,Лекция,2026-11-19T13:00,2026-11-19T14:00,Зал,active\n"
        before=self.db_one("SELECT count(*) FROM personal_event")
        detail=self.make_draft(a,"csv",s)
        self.assertEqual(b.request(detail)[0],404)
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before)
        self.confirm(a,detail)
        result = a.request(detail)[2]
        schedule = unescape(re.search(r'<a\b[^>]* href="([^"]+)"[^>]*>Открыть календарь</a>', result)[1])
        self.assertEqual(parse_qs(urlsplit(schedule).query),
                         {"date": ["2026-11-19"], "view": ["day"], "visibility": ["all"]})
        self.assertIn("Календарный предмет", a.request(schedule)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before+1)
        self.confirm(a,self.make_draft(a,"csv",s))
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before+1)
        output=parse_ics(a.request(EXPORT+RANGE)[2])
        uid, old = next((key,row) for key,row in output.items() if row["SUMMARY"]=="Календарный предмет")
        self.assertEqual(old["DTSTART"],"20261119T100000Z")
        self.assertNotIn(uid,parse_ics(b.request(EXPORT+RANGE)[2]))
        moved=s.replace("T14:00","T15:00").replace("T13:00","T14:00")
        self.confirm(a,self.make_draft(a,"csv",moved))
        self.assertEqual(parse_ics(a.request(EXPORT+RANGE)[2])[uid]["DTSTART"],"20261119T110000Z")
        cancel=TEMPLATE+"sample,,,,,,cancelled\n"
        self.confirm(a,self.make_draft(a,"csv",cancel))
        self.assertEqual(parse_ics(a.request(EXPORT+RANGE)[2])[uid]["STATUS"],"CANCELLED")
        self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),before)

    def test_scheduledraft_errors_remain_unpublished_until_review(self):
        a=self.client("student-a")
        document=json.loads(Path("contracts/schedule-draft.v1.example.json").read_text())
        document["scope"]["timezone"]="Invalid/Zone"
        route=self.make_draft(a,"draft",json.dumps(document,ensure_ascii=False))
        self.assertNotIn("Подтвердить личный импорт",a.request(route)[2])
        zone=route+"/zone"
        fields=a.fields(route,zone)
        self.assertEqual(a.request(zone,{**fields,"zone":"Europe/Moscow"})[0],303)
        self.assertIn("В источнике есть только начало.",a.request(route)[2])
        edit=route+"/row"
        form=a.fields(route,edit)
        self.assertEqual(a.request(edit,{**form,"end":"2026-11-19T14:00","status":"active","reviewed":"1"})[0],303)
        self.confirm(a,route)
        self.assertTrue(any(row["SUMMARY"]=="Базы данных" for row in parse_ics(a.request(EXPORT+RANGE)[2]).values()))
        another=self.make_draft(a,"draft",json.dumps(document))
        c=another+"/cancel"
        self.assertEqual(a.request(c,a.fields(another,c))[0],303)
        self.assertIn("Отменён",a.request(another)[2])

    def test_all_day_utf8_escape_token_scope_and_revocation(self):
        a,b,anonymous=self.client("student-a"),self.client("student-b"),self.client()
        title=("День, проект; "+"длинное название " * 4)+"\\\nBEGIN:VEVENT"
        form=a.fields("/events/new","/events/new")
        event=a.request("/events/new",{**form,"title":title,"all_day":"1","first_day":"2026-11-20","last_day":"2026-11-20"})
        self.assertEqual(event[0],303)
        labels=parse_ics(a.request(EXPORT+RANGE)[2])
        row=next(item for item in labels.values() if item["SUMMARY"].startswith("День"))
        uid=row["UID"]
        self.assertEqual(row["DTSTART;VALUE=DATE"],"20261120")
        self.assertEqual(row["DTEND;VALUE=DATE"],"20261121")
        self.assertIn("\\, ",row["SUMMARY"])
        create=EXPORT+"/subscription"
        fields=a.fields(EXPORT,create)
        payload={**fields,"scope":"personal","first":"2026-11-16","last":"2026-11-28"}
        status,headers,issued=a.request(create,payload)
        self.assertEqual(status,200)
        from re import search
        feed=search(r"(/[a-z]+/feed/[A-Za-z0-9_-]{43})",issued)[1]
        self.assertEqual(a.request(create,payload)[0],409)
        self.assertNotIn(feed,a.request(EXPORT)[2])
        self.assertIn(uid,parse_ics(anonymous.request(feed)[2]))
        delete=event[1]["Location"]+"/delete"
        self.assertEqual(a.request(delete,a.fields(event[1]["Location"],delete))[0],303)
        self.assertEqual(parse_ics(anonymous.request(feed)[2])[uid]["STATUS"],"CANCELLED")
        record=self.db_one("SELECT id FROM calendar_feed")
        revoke=create+"/"+record+"/revoke"
        self.assertEqual(b.request(revoke,{"csrf":b.fields(EXPORT,create)["csrf"]})[0],404)
        self.assertEqual(a.request(revoke,a.fields(EXPORT,revoke))[0],303)
        self.assertEqual(anonymous.request(feed)[0],404)
        self.assertNotIn(feed.rsplit("/",1)[-1],self.db_one("SELECT token_hash FROM calendar_feed"))
