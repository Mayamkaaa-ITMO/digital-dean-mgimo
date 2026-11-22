from dean.pdf import document_text, render_document

from .test_dean import DemoFixture


class ApplicationExpansionTest(DemoFixture):
    def post(self, client, page, action, **data):
        return client.request(action, {**client.fields(page, action), **data})

    def test_draft_correction_issue_private_pdf_and_receipt(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        form = "/services/enrolment-certificate"
        fields = a.fields(form, form)
        draft = a.request(form + "/draft", {**fields, "purpose": ""})
        self.assertEqual(draft[0], 303)
        url = draft[1]["Location"]
        self.assertIn("Черновик", a.request(url)[2])
        self.assertEqual(staff.request(url)[0], 404)
        self.assertEqual(self.post(a, url, url + "/save", purpose="Учебный архив")[0], 303)
        self.assertEqual(a.request(url + "/send", {**a.fields(url, url + "/save"), "purpose": "Учебный архив"})[0], 303)
        self.assertIn(url, staff.request("/staff?service=enrolment-certificate&status=submitted")[2])
        manage = "/staff" + url
        self.assertEqual(self.post(staff, url, manage)[0], 303)
        self.assertEqual(self.post(staff, url, manage + "/needs_changes", note="")[0], 422)
        self.assertEqual(self.post(staff, url, manage + "/needs_changes", note="Уточни цель.")[0], 303)
        self.assertIn("Уточни цель.", a.request(url)[2])
        self.assertIn("требуется исправление", a.request("/notifications")[2])
        a = self.client("student-a")
        edit = {**a.fields(url, url + "/save"), "purpose": "Для личного архива"}
        self.assertEqual(a.request(url + "/send", edit)[0], 303)
        self.assertEqual(a.request(url + "/send", edit)[0], 303)
        self.assertEqual(self.post(staff, url, manage)[0], 303)
        issue = staff.fields(url, manage)
        self.assertEqual(staff.request(manage, issue)[0], 303)
        self.assertEqual(staff.request(manage, issue)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM document"), 1)
        did = self.db_one("SELECT id FROM document")
        path = f"/documents/{did}"
        self.assertIn(path, a.request("/documents")[2])
        preview_path = path + "?preview=1"
        self.assertIn(preview_path, a.request(url)[2])
        self.assertIn(preview_path, a.request("/documents")[2])
        self.assertEqual((b.request(url)[0], b.request(path)[0]), (404, 404))
        self.assertEqual(b.request(preview_path)[0], 404)
        self.assertEqual(self.client().request(preview_path)[0], 303)
        self.assertEqual(self.client().request(path)[0], 303)
        self.assertEqual(staff.request(path)[0], 200)
        status, headers, preview = a.request(preview_path)
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn("Цель получения: Для личного архива", preview)
        self.assertIn(f'{path}?download=1', preview)
        self.assertEqual(staff.request(preview_path)[0], 200)
        status, headers, pdf = a.request(path + "?download=1")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment;"))
        self.assertTrue(pdf.startswith(b"%PDF-1.7") and pdf.endswith(b"%%EOF\n"))
        self.assertIn(b"/ToUnicode", pdf)
        self.assertEqual(a.request(path)[2], pdf)
        self.assertEqual(self.post(a, url, url + "/receive")[0], 303)
        self.assertIn("Документ получен", self.client("student-a").request(url)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM application_history WHERE status='resubmitted'"), 1)

    def test_transcript_refusal_assignment_cancel_and_direct_access(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        base = "/services/academic-transcript"
        fields = a.fields(base, base)
        self.assertEqual(a.request(base, {**fields, "purpose": "Архив", "period": "random"})[0], 422)
        response = a.request(base, {**fields, "purpose": "Архив", "period": "all"})
        self.assertEqual(response[0], 303)
        url = response[1]["Location"]
        manage = "/staff" + url
        staff_id = self.db_one("SELECT id FROM account WHERE role='staff'")
        self.assertEqual(self.post(staff, url, manage + "/assign", assignee=staff_id)[0], 303)
        self.assertIn("Ответственный", a.request(url)[2])
        self.assertEqual(self.post(staff, url, manage + "/comment", note="Рабочая отметка.")[0], 303)
        self.assertEqual(self.post(staff, url, manage)[0], 303)
        self.assertEqual(self.post(staff, url, manage + "/refused", note="Срок закрыт.")[0], 303)
        self.assertIn("Срок закрыт.", a.request(url)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM document"), 0)
        b_csrf = b.fields("/profile", "/logout")["csrf"]
        self.assertEqual(b.request(url + "/send", {"csrf": b_csrf})[0], 404)
        draft = a.request(base + "/draft", a.fields(base, base))[1]["Location"]
        self.assertEqual(self.post(a, draft, draft + "/cancel")[0], 303)
        self.assertEqual(staff.request(draft)[0], 404)
        catalog = a.request("/requests?q=СПРАВКА&category=documents")[2]
        self.assertIn("Справка об обучении", catalog)
        self.assertIn("до 2 дней", catalog)

    def test_transcript_is_issued_and_filters_render_requested_terms(self):
        a, staff = (self.client(name) for name in ("student-a", "staff"))
        form = "/services/academic-transcript"
        result = a.request(form, {**a.fields(form, form), "purpose": "Проверка", "period": "all"})
        url = result[1]["Location"]
        self.assertEqual(self.post(staff, url, "/staff" + url)[0], 303)
        self.assertEqual(self.post(staff, url, "/staff" + url)[0], 303)
        self.assertEqual(self.db_one("SELECT kind FROM document"), "transcript")
        self.assertIn("Все доступные семестры", a.request(url)[2])

    def test_document_preview_reads_all_issued_pages(self):
        lines = [f"Учебная строка {n}" for n in range(36)]
        self.assertEqual(document_text(render_document("Выписка", lines)), tuple(lines))
        self.assertEqual(document_text(b"%PDF-1.7\n"), ())
