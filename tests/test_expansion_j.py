from contextlib import closing
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from dean.clock import at
from dean.db import connect
from dean.fixtures import demo_id
from dean.seed import UNIVERSITY, seed_id

from .test_dean import DemoFixture, Forms


class CollaborationTest(DemoFixture):
    def post(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def setup_team(self, a, b):
        created = self.post(a, "/teams", "/teams", title="Команда эксперимента")
        self.assertEqual(created[0], 303)
        path = created[1]["Location"]
        self.assertEqual(self.post(a, path, path + "/invite", login="student-b")[0], 303)
        iid = self.db_one("SELECT id FROM team_invitation WHERE status='pending'")
        route = "/teams/invitations/" + iid
        self.assertEqual(b.request(path)[0], 404)
        self.assertEqual(self.post(b, route, route + "/accepted")[1]["Location"], path)
        return path

    def test_shared_task_notes_and_moderation_access_revocation(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        path = self.setup_team(a, b)
        with closing(connect(self.path)) as db:
            db.execute("""
                INSERT INTO account (id,username,password_hash,full_name,role,university_id,group_id,period_id)
                SELECT ?,'student-c',password_hash,'Демо Наблюдатель',role,university_id,group_id,period_id
                FROM account WHERE username='student-b'
            """, (uuid4().hex,))
        c = self.client()
        self.assertEqual(c.login("student-c", self.passwords["student-b"])[0], 303)
        self.assertEqual(c.request(path)[0], 404)
        action = path + "/tasks/new"
        due = self.today + timedelta(days=2)
        created = self.post(a, action, action, title="Общий эксперимент", assignee=seed_id("student-b"),
                            priority="normal", due=f"{due}T16:00")
        self.assertEqual(created[0], 303)
        task = created[1]["Location"]
        self.assertIn("Общий эксперимент", b.request(task)[2])
        self.assertNotIn("Общий эксперимент", a.request("/tasks")[2])
        self.assertEqual(c.request(task)[0], 404)
        self.assertIn(task, b.request(f"/schedule?date={due}")[2])
        self.assertEqual(self.post(b, task, task + "/subtasks", title="Собрать образцы данных")[0], 303)
        sid = self.db_one("SELECT id FROM subtask")
        self.assertEqual(self.post(b, task, f"{task}/subtasks/{sid}/complete")[0], 303)
        self.assertIn("1 из 1", a.request(task)[2])
        lesson = seed_id("a1")
        note_form = f"/notes/lesson/{lesson}"
        note_url = self.post(a, "/schedule/" + lesson, note_form, body="Заметка для команды о матрицах")[1]["Location"]
        note = note_url.rsplit("/", 1)[-1]
        self.assertEqual(b.request(note_url)[0], 404)
        self.assertEqual(self.post(a, path, path + "/share-note", note=note)[0], 303)
        shared = path + "/notes/" + note
        self.assertIn("Заметка для команды", b.request(shared)[2])
        query = "/search?" + urlencode({"q": "матрицах"})
        self.assertIn(shared, b.request(query)[2])
        self.assertNotIn(shared, c.request(query)[2])
        self.assertEqual(self.post(a, shared, shared + "/revoke")[0], 303)
        self.assertEqual(b.request(shared)[0], 404)
        self.assertNotIn(shared, b.request(query)[2])
        self.assertNotIn(shared, b.request("/notifications")[2])
        self.assertEqual(self.post(a, path, path + "/post", body="<b>Уточни пример</b>")[0], 303)
        self.assertIn("&lt;b&gt;Уточни пример", b.request(path)[2])
        message = self.db_one("SELECT id FROM team_message")
        self.assertEqual(self.post(b, path, path + "/report/" + message, reason="Проверка модерации")[0], 303)
        rid = self.db_one("SELECT id FROM team_report")
        moderate = "/staff/moderation/" + rid
        self.assertEqual(self.post(staff, "/staff/moderation", moderate, outcome="hidden", decision="Демо-проверка")[0], 303)
        self.assertNotIn("Уточни пример", b.request(path)[2])
        self.assertEqual(self.post(a, path, path + "/remove/" + seed_id("student-b"))[0], 303)
        self.assertEqual(b.request(path)[0], 404)
        self.assertEqual(b.request(task)[0], 404)
        self.assertNotIn(task, b.request(f"/schedule?date={due}")[2])
        self.assertNotIn(path, b.request("/notifications")[2])

    def test_availability_consent_meeting_move_and_cancel(self):
        a, b = self.client("student-a"), self.client("student-b")
        path = self.setup_team(a, b)
        day = self.today + timedelta(days=2)
        query = path + "/free?" + urlencode({"from": str(day), "to": str(day), "minutes": 60})
        blocked, _, html = a.request(query)
        self.assertEqual(blocked, 422)
        self.assertIn("Для подбора дай согласие сам", html)
        self.assertIn(f'href="{path}"', html)
        self.assertIn(f'action="{path}/consent"', html)
        consent = path + "/consent"
        accepted = a.request(consent, Forms(html).forms[consent])
        self.assertEqual((accepted[0], accepted[1]["Location"]), (303, query))
        retry, _, html = a.request(query)
        self.assertEqual(retry, 422)
        self.assertIn(f'value="{day}"', html)
        self.assertEqual(self.post(b, path, path + "/consent", allow="1")[0], 303)
        self.assertIn("Свободные интервалы", a.request(query)[2])
        action = path + "/meetings"
        proposed = {**a.fields(query, action), "title": "Демо-совместная подготовка",
                    "start": f"{day}T16:00", "end": f"{day}T17:00"}
        self.assertEqual(self.post(b, path, path + "/consent", allow="0")[0], 303)
        self.assertEqual(a.request(action, proposed)[0], 422)
        self.assertEqual(self.post(b, path, path + "/consent", allow="1")[0], 303)
        created = a.request(action, proposed)
        self.assertEqual(created[0], 303)
        invite = created[1]["Location"]
        self.assertEqual(a.request(action, proposed)[1]["Location"], invite)
        self.assertIn("Ожидает ответа", b.request(invite)[2])
        self.assertEqual(self.post(b, invite, invite + "/accepted")[0], 303)
        self.assertIn(invite, b.request(f"/schedule?date={day}")[2])
        self.assertEqual(self.post(a, invite, invite + "/move", start=f"{day}T18:00", end=f"{day}T19:00")[0], 303)
        self.assertNotIn(invite, b.request(f"/schedule?date={day}")[2])
        self.assertEqual(self.post(b, invite, invite + "/accepted")[0], 303)
        self.assertIn(invite, self.read_after_restart(b, f"/schedule?date={day}"))
        self.assertEqual(self.post(a, invite, invite + "/cancel")[0], 303)
        self.assertIn("Отменена", b.request(invite)[2])
        self.assertNotIn(invite, b.request(f"/schedule?date={day}")[2])
        self.assertIn("Совместная встреча отменена", b.request("/notifications")[2])

    def test_club_membership_and_invite_revoke(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        club = demo_id(UNIVERSITY, "club", "applied-science")
        club_path = "/teams/" + club
        self.assertEqual(a.request(club_path)[0], 404)
        service = "/services/club-join"
        url = self.post(a, service, service, club=club, details="Хочу участвовать в экспериментах.")[1]["Location"]
        self.assertEqual(a.request(club_path)[0], 404)
        action = "/staff" + url
        self.assertEqual(self.post(staff, url, action)[0], 303)
        self.assertEqual(self.post(staff, url, action, note="Принято в демо-клуб.")[0], 303)
        self.assertIn("Практикум по командному эксперименту", a.request(club_path)[2])
        self.assertEqual(b.request(club_path)[0], 404)
        path = self.post(a, "/teams", "/teams", title="Другая демо-команда")[1]["Location"]
        self.assertEqual(self.post(a, path, path + "/invite", login="student-b")[0], 303)
        iid = self.db_one("SELECT id FROM team_invitation")
        pending = "/teams/invitations/" + iid
        payload = b.fields(pending, pending + "/accepted")
        self.assertEqual(self.post(a, path, f"{path}/invites/{iid}/revoke")[0], 303)
        self.assertEqual(b.request(pending + "/accepted", payload)[0], 409)
        self.assertEqual(b.request(path)[0], 404)
        self.assertEqual(self.post(a, path, path + "/invite", login="student-b")[0], 303)
        until = datetime.fromisoformat(self.db_one("SELECT expires_at FROM team_invitation WHERE status='pending'"))
        fresh_id = self.db_one("SELECT id FROM team_invitation WHERE status='pending'")
        fresh = "/teams/invitations/" + fresh_id
        fields = b.fields(fresh, fresh + "/accepted")
        with at(until):
            self.assertEqual(b.request(fresh + "/accepted", fields)[0], 409)
