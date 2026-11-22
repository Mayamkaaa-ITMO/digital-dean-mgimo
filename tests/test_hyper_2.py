from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from dean import clock, coursework
from dean.db import connect
from dean.seed import UNIVERSITY, seed_id
from tests.test_dean import DemoFixture

TIME=datetime.fromisoformat("2026-11-18T09:00:00+00:00")
DUE="2026-11-19T17:00"


class CourseworkTest(DemoFixture):
    def create(self,staff,mode="either",late=False):
        with closing(connect(self.path)) as db:
            group=db.execute("SELECT group_id FROM account WHERE username='student-a'").fetchone()[0]
            coursework.grant_demo(db,"staff",group,"algorithms")
        path="/staff/work/new"
        fields=staff.fields(path,path)
        intake={**fields,"scope":"|".join((group,self.period,"algorithms")),"title":"Демо-работа по алгоритмам",
                "description":"Выполни разбор <примера>.","due":DUE,"response_mode":mode,
                "late":"1" if late else "","attempt_limit":"2","rubric":"Обоснование | 7\nТочность | 3"}
        result=staff.request(path,intake)
        self.assertEqual(result[0],303,result[2][:100])
        outline=result[1]["Location"]
        action=outline+"/publish"
        publish=staff.fields(outline,action)
        self.assertEqual(staff.request(action,publish)[0],303)
        self.assertEqual(staff.request(action,publish)[0],303)
        wid=self.db_one("SELECT id FROM coursework_student")
        return outline,"/work/"+wid,"/staff/work/review/"+wid

    def test_file_versions_review_feedback_and_zero_to_existing_gradebook(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        with clock.at(TIME):
            assignment,detail,review=self.create(staff)
            self.assertEqual(self.db_one("SELECT count(*) FROM coursework_student"),1)
            task = self.db_one("SELECT task_id FROM coursework_student")
            def task_state():
                return self.db_one("SELECT state FROM task WHERE id=?", (task,))

            self.assertEqual(task_state(), "todo")
            self.assertEqual(b.request(detail)[0],404)
            self.assertNotIn("Демо-работа",b.request("/work")[2])
            self.assertIn("Опубликована учебная работа",a.request("/notifications")[2])
            save=detail+"/draft"
            self.assertEqual(a.request(save,{**a.fields(detail,save),"answer":"Первый черновик"})[0],303)
            self.assertEqual(self.db_one("SELECT count(*) FROM coursework_attempt"),0)
            self.assertEqual(task_state(), "todo")
            upload=detail+"/attachment"
            fields=a.fields(detail,upload)
            boundary="CourseworkFixtures"
            raw=b""
            for name in ("csrf","request_id","version"):
                raw+=f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{fields[name]}\r\n'.encode()
            raw+=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="answer.txt"\r\n'
                  'Content-Type: text/plain\r\n\r\nДемонстрационный текст\r\n'
                  f'--{boundary}--\r\n').encode()
            self.assertEqual(a.request(upload,raw,CONTENT_TYPE="multipart/form-data; boundary="+boundary)[0],303)
            file="/work/files/"+self.db_one("SELECT id FROM coursework_file")
            self.assertEqual(staff.request(file)[0],404)
            self.assertEqual(b.request(file)[0],404)
            publish=detail+"/submit"
            sent={**a.fields(detail,save),"answer":"Первый ответ"}
            self.assertEqual(a.request(publish,sent)[0],303)
            self.assertEqual(a.request(publish,sent)[0],303)
            self.assertEqual(task_state(), "done")
            self.assertEqual(staff.request(file)[0],200)
            mark=review+"/draft"
            teacher={**staff.fields(review,mark),"feedback":"Закрытая ремарка"}
            self.assertEqual(staff.request(mark,teacher)[0],303)
            self.assertNotIn("Закрытая ремарка",a.request(detail)[2])
            returned={**staff.fields(review,mark),"feedback":"Уточни шаг рассуждения"}
            self.assertEqual(staff.request(review+"/return",returned)[0],303)
            self.assertEqual(task_state(), "todo")
            self.assertIn("Уточни шаг рассуждения",a.request(detail)[2])

            edit=assignment+"/edit"
            changed={**staff.fields(edit,edit),"description":"Новые условия","response_mode":"either",
                     "rubric":"Другая шкала | 50"}
            self.assertEqual(staff.request(edit,changed)[0],303)
            self.assertIn("максимум: 10",a.request(detail)[2])
            second={**a.fields(detail,save),"answer":"Новая версия ответа"}
            self.assertEqual(a.request(publish,second)[0],303)
            final={**staff.fields(review,mark),"feedback":"Проверено", "points_0":"0","points_1":"0"}
            self.assertEqual(staff.request(review+"/publish",final)[0],303)
            self.assertEqual(staff.request(review+"/publish",final)[0],303)
            self.assertEqual(task_state(), "done")
            self.assertEqual(self.db_one("SELECT points FROM assessment WHERE code LIKE 'work-%'"),0)
            self.assertEqual(self.db_one("SELECT count(*) FROM coursework_attempt"),2)
            self.assertIn("Результат опубликован",a.request("/notifications")[2])
            archive=assignment+"/archive"
            self.assertEqual(staff.request(archive,staff.fields(assignment,archive))[0],303)
        self.assertIn("Опубликованный результат: 0",self.read_after_restart(a,detail))
        self.assertIn("Первый ответ",a.request(detail)[2])
        self.assertEqual(a.request(file)[0],200)

    def test_late_policy_concurrency_and_staff_scope(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        bad="/staff/work/new"
        token=staff.fields("/staff","/logout")["csrf"]
        denied={"csrf":token,"request_id":uuid4().hex,"title":"Работа","description":"Задача","due":DUE,
                "response_mode":"text","attempt_limit":"1","rubric":"Ответ | 5","scope":"dummy|bad|x"}
        with clock.at(TIME):
            self.assertEqual(staff.request(bad,denied)[0],403)
            _,detail,_=self.create(staff,mode="text")
            route=detail+"/submit"
            data={**a.fields(detail,detail+"/draft"),"answer":"решение"}
        with clock.at(TIME+timedelta(days=3)):
            self.assertEqual(a.request(route,data)[0],409)
        self.assertEqual(self.db_one("SELECT count(*) FROM coursework_attempt"),0)
        def send(nonce):
            with clock.at(TIME):
                return a.request(route,{**data,"request_id":nonce})[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes=sorted(pool.map(send,(uuid4().hex,uuid4().hex)))
        self.assertEqual(outcomes,[303,409])
        self.assertEqual(self.db_one("SELECT count(*) FROM coursework_attempt"),1)
        self.assertIsNone(self.db_one("SELECT points FROM assessment WHERE code LIKE 'work-%'"))

    @patch.dict("os.environ", {"DEAN_CAMPUS_LAB": "1", "DEAN_PRODUCT": "1"})
    def test_student_navigation_links_coursework_and_scoped_recordings(self):
        a,b,staff=(self.client(n) for n in ("student-a","student-b","staff"))
        with clock.at(TIME):
            _,work,_=self.create(staff,mode="text")
            with closing(connect(self.path)) as db:
                task, grade = db.execute("""SELECT w.task_id,c.result_id FROM coursework_student w
                                          JOIN assessment c ON c.id=w.assessment_id""").fetchone()
                fixtures = (
                    ("course-recording", "demo-ai", "algorithms", "group", seed_id("staff")),
                    ("other-discipline", "demo-ai", "databases", "group", seed_id("staff")),
                    ("other-group", "demo-energy", "algorithms", "group", seed_id("staff")),
                    ("private-recording", "demo-ai", "algorithms", "private", seed_id("student-a")),
                )
                for label, group, subject, visibility, owner in fixtures:
                    db.execute("""INSERT INTO lab_lecture
                        (id,owner,university_id,group_id,period_id,subject_id,title,created,ended,
                         transcript,notes,homework,recording_url,visibility)
                        VALUES (?,?,?,?,?,?,?,?,?,'Расшифровка','Конспект','Задание',?,?)""",
                        (seed_id(label), owner, UNIVERSITY, group, self.period, subject, label,
                         TIME.isoformat(), TIME.isoformat(), "https://example.com/video.mp4", visibility))
            self.assertIn(work, a.request("/tasks/"+task)[2])
            self.assertIn("/grades/"+grade, a.request(work)[2])
            self.assertIn(work, a.request("/grades/"+grade)[2])
            lesson = "/schedule/"+seed_id("a1")
            _,_,body = a.request(lesson)
            self.assertIn(work, body)
            self.assertIn("/lectures/"+seed_id("course-recording")+"#notes", body)
            self.assertIn("/lectures/"+seed_id("course-recording")+"#homework", body)
            for label, *_ in fixtures[1:]:
                self.assertNotIn("/lectures/"+seed_id(label), body)
            self.assertEqual(b.request(lesson)[0], 404)
            self.assertEqual(b.request(work)[0], 404)

            route = "/tasks/new"
            new = b.request(route, {**b.fields(route,route), "title": "Проверить расчёт", "subject": "physics",
                                    "priority": "normal", "due": DUE})
            self.assertEqual(new[0], 303)
            task_body = b.request(new[1]["Location"])[2]
            self.assertIn("Способ сдачи здесь не указан", task_body)
            self.assertNotIn(work, task_body)
        with clock.at(TIME+timedelta(days=3)):
            work_body = a.request(work)[2]
            self.assertIn("Срок сдачи прошёл", work_body)
            self.assertNotIn(f'formaction="{work}/submit"', work_body)
