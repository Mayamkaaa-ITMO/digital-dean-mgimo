from contextlib import closing
import json
import re

from dean import services
from dean.db import connect, write
from dean.domain import timestamp
from tests.test_dean import DemoFixture

DATA="/profile/data"
EXPORT=DATA+"/export"
SESS="/profile/sessions"


def upload(client, action, fields, filename, data):
    boundary="TransferFixture"
    request=b""
    for key in ("csrf","request_id"):
        request+=f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{fields[key]}\r\n'.encode()
    request+=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
              'Content-Type: application/json\r\n\r\n').encode()
    request+=data.encode()+f"\r\n--{boundary}--\r\n".encode()
    return client.request(action,request,CONTENT_TYPE="multipart/form-data; boundary="+boundary)


class ProfileToolsTest(DemoFixture):
    def test_personal_transfer_conflicts_handle_owned_data_and_authorization(self):
        a,b=self.client("student-a"),self.client("student-b")
        fields=a.fields("/tasks/new","/tasks/new")
        task="/tasks/new"
        created=a.request(task,{**fields,"title":"Задача переноса A","priority":"normal"})
        self.assertEqual(created[0],303)
        original=created[1]["Location"]
        note_route="/notes/task/"+original.rsplit("/",1)[1]
        note=a.fields(original,note_route)
        self.assertEqual(a.request(note_route,{**note,"body":"Заметка переноса A"})[0],303)
        snapshot=json.loads(a.request(EXPORT)[2])
        self.assertNotIn("Задача переноса A",b.request(EXPORT)[2])
        self.assertNotIn(a.cookies["dean_session"],str(snapshot))
        self.assertEqual(set(snapshot),{"format","version","settings","tasks","notes","events"})
        action=DATA+"/upload/keep"
        fields=b.fields(DATA,action)
        approved=upload(b,action,fields,"backup.json",json.dumps(snapshot,ensure_ascii=False))
        self.assertEqual(approved[0],303)
        preview=approved[1]["Location"]
        self.assertEqual(a.request(preview)[0],404)
        self.assertNotIn("Задача переноса A",b.request("/tasks")[2])
        confirm=preview+"/apply"
        fields=b.fields(preview,confirm)
        self.assertEqual(b.request(confirm,fields)[0],303)
        self.assertEqual(b.request(confirm,fields)[0],303)
        self.assertIn("Задача переноса A",b.request("/tasks")[2])
        self.assertEqual(b.request(original)[0],404)
        again=json.dumps(snapshot,ensure_ascii=False)
        review=upload(b,action,b.fields(DATA,action),"backup.json",again)[1]["Location"]
        self.assertIn("Добавится 0",b.request(review)[2])
        inserted=[row for row in json.loads(b.request(EXPORT)[2])["tasks"] if row["title"]=="Задача переноса A"]
        self.assertEqual(len(inserted),1)
        own=b.request("/tasks/"+inserted[0]["id"]+"/edit")
        self.assertEqual(own[0],200)
        change=original+"/edit"
        update=a.request(change,{**a.fields(change,change),"title":"Правка после копии","priority":"normal"})
        self.assertEqual(update[0],303)
        checked=upload(a,action,a.fields(DATA,action),"backup.json",again)[1]["Location"]
        self.assertIn("Конфликты 1",a.request(checked)[2])
        apply=checked+"/apply"
        self.assertEqual(a.request(apply,a.fields(checked,apply))[0],303)
        self.assertIn("Правка после копии",a.request(original)[2])
        snapshot["tasks"][-1]["role"]="staff"
        self.assertEqual(upload(b,action,b.fields(DATA,action),"bad.json",json.dumps(snapshot))[0],422)
        self.assertEqual(b.request("/staff")[0],403)

    def test_session_handles_never_expose_tokens_and_only_revoke_own(self):
        a,other,b=self.client("student-a"),self.client("student-a"),self.client("student-b")
        code,_,html=a.request(SESS)
        self.assertEqual(code,200)
        self.assertNotIn(other.cookies["dean_session"],html)
        other_route=re.search(r'action="(/profile/sessions/[a-f0-9]{32}/revoke)"',html)[1]
        token=b.fields(SESS,SESS+"/revoke-others")["csrf"]
        self.assertEqual(b.request(other_route,{"csrf":token})[0],404)
        self.assertEqual(a.request(other_route,a.fields(SESS,other_route))[0],303)
        self.assertEqual(other.request(SESS)[0],303)
        self.assertEqual(a.request(SESS)[0],200)
        self.assertEqual(b.request(SESS)[0],200)

    def test_profile_favorites_privacy_and_staff_ticket_scope(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        links="/profile/links"
        fields=a.fields(links,links)
        suspect={"kind":"personal","title":"Ресурс A","url":"javascript:alert(1)"}
        self.assertEqual(a.request(links,{**fields,**suspect})[0],422)
        self.assertEqual(a.request(links,{**fields,**suspect,"url":"https://example.test/notes"})[0],303)
        self.assertIn("Ресурс A",a.request(links)[2])
        self.assertNotIn("Ресурс A",b.request(links)[2])
        with closing(connect(self.path)) as db,write(db):
            account=db.execute("SELECT * FROM account WHERE username='student-a'").fetchone()
            services.notify(db,account["university_id"],account["id"],"grade","", "test:grade",
                            "Оценка 89", "Закрытое число 89",timestamp())
        self.assertIn("Закрытое число",a.request("/notifications")[2])
        privacy="/profile/privacy"
        self.assertEqual(a.request(privacy,{**a.fields(privacy,privacy),"hide":"1"})[0],303)
        body=a.request("/notifications")[2]
        self.assertNotIn("Закрытое число",body)
        self.assertIn("Личное обновление",body)
        path="/support"
        result=a.request(path,{**a.fields(path,path),"title":"Вопрос A","body":"Контекст <A>","topic":"calendar","context":"1"})
        self.assertEqual(result[0],303)
        ticket=result[1]["Location"]
        self.assertIn("&lt;A&gt;",a.request(ticket)[2])
        self.assertEqual(b.request(ticket)[0],404)
        ticket="/staff"+ticket
        self.assertEqual(a.request(ticket)[0],403)
        self.assertEqual(staff.request(ticket,staff.fields(ticket,ticket))[0],303)
        self.assertIn("В работе",a.request(result[1]["Location"])[2])
        self.assertEqual(staff.request(ticket, staff.fields(ticket,ticket))[0],422)
        self.assertEqual(staff.request(ticket,{**staff.fields(ticket,ticket),"reply":"Решено <QA>"})[0],303)
        self.assertIn("Решено &lt;QA&gt;",a.request(result[1]["Location"])[2])
        self.assertIn(result[1]["Location"],a.request("/notifications")[2])
        self.assertEqual(b.request(result[1]["Location"])[0],404)
        secret=b.request(path,{**b.fields(path,path),"title":"Закрытая группа","body":"Внутреннее"})[1]["Location"]
        self.assertEqual(staff.request("/staff"+secret)[0],404)
