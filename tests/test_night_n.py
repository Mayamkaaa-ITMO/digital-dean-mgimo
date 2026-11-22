from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from dean import clock
from tests.test_dean import DemoFixture

WHEN = datetime.fromisoformat("2027-02-01T07:00:00+00:00")
PREP = "/study/preparation"
TIMER = "/study/timer"


class PreparationTest(DemoFixture):
    def task(self, client):
        form = client.fields("/tasks/new", "/tasks/new")
        response = client.request("/tasks/new", {**form, "title": "Доказать лемму", "priority": "high"})
        self.assertEqual(response[0], 303)
        detail = response[1]["Location"]
        effort = detail+"/effort"
        self.assertEqual(client.request(effort, {**client.fields(detail, effort), "minutes":"100"})[0], 303)
        return detail

    def proposal(self, client, task, hours="12:00"):
        fields = client.fields(PREP,PREP)
        result = client.request(PREP, {**fields, "task": task.rsplit("/",1)[1], "exam": "",
                                      "deadline":"2027-02-01T15:00", "first":"2027-02-01",
                                      "from":"11:00", "to":hours, "daily":"40","block":"40","rest":"10"})
        self.assertEqual(result[0],303)
        return result[1]["Location"]

    def test_preparation_respects_load_leftovers_and_idempotent_confirmation(self):
        a,b=self.client("student-a"), self.client("student-b")
        with clock.at(WHEN):
            task=self.task(a)
            baseline=self.db_one("SELECT count(*) FROM personal_event")
            plan=self.proposal(a,task)
            self.assertIn("Осталось 60 мин", a.request(plan)[2])
            self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),baseline)
            self.assertEqual(b.request(plan)[0],404)
            move=plan+"/move"
            edit=a.fields(plan,move)
            self.assertEqual(a.request(move,{**edit,"starts":"2027-02-01T11:15"})[0],303)
            confirm=plan+"/confirm"
            form=a.fields(plan,confirm)
            self.assertEqual(a.request(confirm,form)[0],303)
            self.assertEqual(a.request(confirm,form)[0],303)
            self.assertEqual(self.db_one("SELECT count(*) FROM personal_event"),baseline+1)
            self.assertIn("11:15", a.request(plan)[2])
        self.assertIn("Сохранённые события",self.read_after_restart(a,plan))

    def test_preparation_draft_rejects_a_new_conflict_or_completed_task(self):
        a=self.client("student-a")
        with clock.at(WHEN):
            task=self.task(a)
            plan=self.proposal(a,task)
            confirm=plan+"/confirm"
            submitted=a.fields(plan,confirm)
            form=a.fields("/events/new","/events/new")
            self.assertEqual(a.request("/events/new",{**form,"title":"Новая встреча", "starts":"2027-02-01T11:00",
                                                    "ends":"2027-02-01T12:00"})[0],303)
            self.assertEqual(a.request(confirm,submitted)[0],409)
            next_plan=self.proposal(a,task,hours="13:00")
            action=task+"/complete"
            self.assertEqual(a.request(action,a.fields(task,action))[0],303)
            next_confirm=next_plan+"/confirm"
            self.assertEqual(a.request(next_confirm,a.fields(next_plan,next_confirm))[0],409)
            self.assertEqual(self.db_one("SELECT count(*) FROM study_preparation_event"),0)

    def test_timer_handles_parallel_start_pause_and_repeat_finish(self):
        a,b=self.client("student-a"), self.client("student-b")
        form=a.fields(TIMER,TIMER)
        def start(key):
            with clock.at(WHEN):
                return a.request(TIMER,{**form,"request_id":key})[0]
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(list(executor.map(start, (form["request_id"], uuid4().hex))), [303,303])
        sid=self.db_one("SELECT id FROM study_timer")
        paused=f"{TIMER}/{sid}/pause"
        with clock.at(WHEN+timedelta(seconds=75)):
            self.assertIn("1 мин 15 с",a.request(TIMER)[2])
            self.assertEqual(a.request(paused,a.fields(TIMER,paused))[0],303)
        resume=f"{TIMER}/{sid}/resume"
        with clock.at(WHEN+timedelta(seconds=60)):
            self.assertEqual(a.request(resume,a.fields(TIMER,resume))[0],409)
        with clock.at(WHEN+timedelta(minutes=4)):
            payload=a.fields(TIMER,resume)
            self.assertEqual(b.request(resume,{**payload,"csrf":b.fields(TIMER,TIMER)["csrf"]})[0],404)
            self.assertEqual(a.request(resume,payload)[0],303)
        finish=f"{TIMER}/{sid}/finish"
        with clock.at(WHEN+timedelta(minutes=5)):
            data=a.fields(TIMER,finish)
            self.assertEqual(a.request(finish,data)[0],303)
            self.assertEqual(a.request(finish,data)[0],303)
        self.assertIn("2 мин 15 с",self.read_after_restart(a,TIMER))
        self.assertEqual(self.db_one("SELECT count(*) FROM study_timer"),1)

    def test_materials_are_validated_and_owner_only_in_search_and_download(self):
        a,b=self.client("student-a"), self.client("student-b")
        index="/study/subjects"
        created=a.request(index,{**a.fields(index,index),"title":"Мой предмет"})
        self.assertEqual(created[0],303)
        materials=created[1]["Location"]+"/materials"
        form=a.fields(materials,materials)
        self.assertEqual(a.request(materials,{**form,"kind":"url","title":"Ошибка","value":"javascript:alert(1)"})[0],422)
        note={**form,"kind":"note","title":"Конспект","value":"Изолированный фрагмент <b>безопасен</b>."}
        self.assertEqual(a.request(materials,note)[0],303)
        query="/search?"+urlencode({"q":"Изолированный"})
        self.assertIn("Конспект",a.request(query)[2])
        self.assertNotIn("Конспект",b.request(query)[2])
        self.assertEqual(b.request(materials)[0],404)
        self.assertIn("&lt;b&gt;",a.request(materials)[2])

        boundary = "StudyTestBoundary"
        upload = b""
        for name in ("csrf", "request_id"):
            val = form[name] if name == "csrf" else uuid4().hex
            upload += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{val}\r\n'.encode()
        upload += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="lesson.txt"\r\n'
                   f'Content-Type: text/plain\r\n\r\nЧастный документ\r\n--{boundary}--\r\n').encode()
        content_type=f"multipart/form-data; boundary={boundary}"
        self.assertEqual(a.request(materials,upload.replace(b"lesson.txt",b"lesson.html"), CONTENT_TYPE=content_type)[0],422)
        self.assertEqual(a.request(materials,upload, CONTENT_TYPE=content_type)[0],303)
        mid=self.db_one("SELECT id FROM study_material WHERE kind='file'")
        file="/study/materials/"+mid+"/download"
        self.assertEqual(a.request(file)[2],"Частный документ".encode())
        self.assertEqual(b.request(file)[0],404)
