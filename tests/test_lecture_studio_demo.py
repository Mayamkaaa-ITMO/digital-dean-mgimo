from contextlib import closing
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch
from wsgiref.util import setup_testing_defaults
from wsgiref.validate import validator

from dean import coursework, lecture_studio as studio
from dean.db import connect
from dean.web import create_app

from .test_dean import DemoFixture


class LectureStudioTest(DemoFixture):
    def test_recording_seek_teacher_review_publish_and_restart(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        self.assertEqual(staff.request(studio.PATH)[0], 404)
        with patch.dict("os.environ", {"DEAN_EXT_LECTURE_STUDIO": "1"}):
            self.assertEqual(a.request(studio.PATH)[0], 404)
            self.assertEqual(a.request(studio.RECORDING)[0], 404)
            self.assertIn("после назначения группы", staff.request(studio.PATH)[2])
            group = self.db_one("SELECT group_id FROM account WHERE username='student-a'")
            with closing(connect(self.path)) as db:
                coursework.grant_demo(db, "staff", group, "algorithms")
            rendered = staff.request(studio.PATH + "?at=8")
            self.assertIn(studio.RECORDING + "#t=8", rendered[2])
            self.assertIn("Текст перенесён из сценария", rendered[2])
            self.assertIn("media-src 'self'", rendered[1]["Content-Security-Policy"])
            self.assertIn("Lecture Studio", staff.request("/staff/work")[2])

            environ = {"PATH_INFO": studio.RECORDING, "REQUEST_METHOD": "GET", "wsgi.input": BytesIO(),
                       "HTTP_RANGE": "bytes=0-31",
                       "HTTP_COOKIE": "; ".join(f"{key}={value}" for key, value in staff.cookies.items())}
            setup_testing_defaults(environ)
            replies = []
            data = b"".join(self.app(environ, lambda status, headers: replies.append((status, dict(headers)))))
            self.assertTrue(replies[0][0].startswith("206"))
            self.assertEqual(replies[0][1]["Content-Type"], "video/mp4")
            self.assertEqual(len(data), 32)
            self.assertEqual(data[4:8], b"ftyp")

            due = f"{self.today + timedelta(days=7)}T17:00"
            fields = {**staff.fields(studio.PATH, studio.PATH), "scope": f"{group}|{self.period}|algorithms",
                      "title": "Проверено преподавателем", "description": studio.PROPOSAL, "due": due, "rubric": "Точность | 10"}
            self.assertEqual(staff.request(studio.PATH, fields)[0], 422)
            accepted = {**fields, "reviewed": "1"}
            result = staff.request(studio.PATH, accepted)
            self.assertEqual(result[0], 303, result[2])
            assignment = result[1]["Location"]
            self.assertEqual(staff.request(studio.PATH, accepted)[1]["Location"], assignment)
            self.assertNotIn(fields["title"], a.request("/work")[2])
            action = assignment + "/publish"
            self.assertEqual(staff.request(action, staff.fields(assignment, action))[0], 303)
            self.assertEqual(self.db_one("SELECT count(*) FROM coursework_assignment"), 1)
            a.app = validator(create_app(self.path))
            self.assertIn(fields["title"], a.request("/work")[2])
            self.assertNotIn(fields["title"], b.request("/work")[2])
