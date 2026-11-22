from unittest.mock import patch

from tests.test_dean import Client, DemoFixture


LESSON = "/lecture-library/semantic-web/intro"


class LectureLibraryUITest(DemoFixture):
    def test_lesson_material_links_and_practice_feedback(self):
        student = self.client("student-a")
        status, headers, lesson = student.request(LESSON + "?t=221")
        self.assertEqual(status, 200)
        self.assertIn("media-src 'self'", headers["Content-Security-Policy"])
        self.assertIn('data-start="221"', lesson)
        self.assertIn('href="#transcript"', lesson)
        self.assertIn('aria-label="Вопрос 1, нет ответа"', lesson)
        self.assertIn('<progress value="0"', lesson)
        for document in ("notes", "homework"):
            path = f"{LESSON}/{document}.pdf"
            self.assertIn(f'href="{path}"', lesson)
            self.assertIn(f'href="{path}?download=1"', lesson)
            for suffix, mode in (("", "inline"), ("?download=1", "attachment")):
                result, headers, content = student.request(path + suffix)
                self.assertEqual(result, 200)
                self.assertEqual(headers["Content-Type"], "application/pdf")
                self.assertTrue(headers["Content-Disposition"].startswith(mode))
                self.assertTrue(content.startswith(b"%PDF"))

        payload = student.fields(LESSON, LESSON + "/quiz") | {"question": "0", "answer": "1"}
        status, headers, _ = student.request(LESSON + "/quiz", payload)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], LESSON + "#question-0")
        _, _, reviewed = student.request(headers["Location"])
        self.assertIn('<progress value="1"', reviewed)
        self.assertIn('aria-label="Вопрос 1, есть ошибка"', reviewed)
        self.assertIn('data-quiz-feedback data-answer-state="incorrect"', reviewed)
        self.assertIn("Пока неверно.", reviewed)

        self.assertEqual(student.request(LESSON + "/quiz", payload | {"answer": "0"})[0], 303)
        _, _, corrected = student.request(LESSON)
        self.assertIn('<progress value="1"', corrected)
        self.assertIn('aria-label="Вопрос 1, верно"', corrected)
        self.assertIn('data-quiz-feedback data-answer-state="correct"', corrected)
        self.assertIn("Правильные ответы <strong>1</strong>", corrected)

    @patch.dict("os.environ", {"DEAN_CAMPUS_LAB": "1"})
    def test_catalog_import_action_for_staff_and_students(self):
        staff, student = Client(self.app), Client(self.app)
        for client, login in ((staff, "staff"), (student, "student-a")):
            self.assertEqual(client.login(login, self.passwords[login])[0], 303)
        self.assertIn('href="/lectures/import"', staff.request("/lecture-library")[2])
        self.assertIn('href="/lectures/import"', student.request("/lecture-library")[2])
        self.assertIn('href="/lectures/import"', student.request("/lecture-library/semantic-web")[2])
        status, _, html = staff.request("/lectures/import")
        self.assertEqual(status, 200)
        self.assertIn('action="/lectures/import/link"', html)
        self.assertIn('action="/lectures/import/upload"', html)
        self.assertIn('enctype="multipart/form-data"', html)
        status, _, html = student.request("/lectures/import")
        self.assertEqual(status, 200)
        self.assertIn("Запись и материалы доступны только тебе.", html)
        self.assertIn('value="link" checked', html)
