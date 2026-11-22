import struct
import zlib

from dean.attachment import PNG, png_chunk
from dean.db import connect
from contextlib import closing

from .test_dean import DemoFixture


class ApplicationVersionTest(DemoFixture):
    def post(self, client, url, action, **data):
        return client.request(action, {**client.fields(url, action), **data})

    def upload(self, client, url, name, content, *, fields=None):
        action = url + "/attachments"
        fields = fields if fields is not None else client.fields(url, action)
        boundary = "TestBoundaryDeans"
        body = b""
        for key in ("csrf", "request_id", "version"):
            body += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{fields[key]}\r\n'.encode()
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n" + content + f"\r\n--{boundary}--\r\n".encode()
        return client.request(action, body, CONTENT_TYPE=f"multipart/form-data; boundary={boundary}")

    def test_versioned_credit_transfer_and_private_attachment(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        start = "/services/credit-transfer"
        url = a.request(start + "/draft", a.fields(start, start))[1]["Location"]
        data = dict(subject="databases", source_course="Демо-курс", hours="36", details="Исходное обоснование")
        send = url + "/send"
        self.assertEqual(a.request(send, {**a.fields(url, url + "/save"), **data})[0], 422)
        self.assertEqual(self.upload(a, url, "example.txt", b"synthetic transcript")[0], 303)
        aid = url.rsplit("/", 1)[-1]
        fid = self.db_one("SELECT id FROM application_attachment WHERE application_id=?", (aid,))
        download = "/attachments/" + fid
        self.assertEqual(staff.request(download)[0], 404)
        self.assertEqual(b.request(download)[0], 404)
        payload = {**a.fields(url, url + "/save"), **data}
        response = a.request(send, payload)
        self.assertEqual(response[0], 303)
        self.assertEqual(a.request(send, payload)[0], 303)
        self.assertEqual(staff.request(download)[2], b"synthetic transcript")
        staff_action = "/staff" + url
        self.assertEqual(self.post(staff, url, staff_action)[0], 303)
        self.assertEqual(self.post(staff, url, staff_action + "/needs_changes", note="Уточни курс.")[0], 303)
        self.assertEqual(self.post(a, url, url + "/comment", note="Уточняю название.")[0], 303)
        self.assertEqual(self.post(a, url, url + "/save", **{**data, "source_course": "Новая версия курса"})[0], 303)
        self.assertNotIn("Новая версия курса", staff.request(url)[2])
        self.assertEqual(self.upload(a, url, "addition.txt", b"new version")[0], 303)
        second = self.db_one("SELECT id FROM application_attachment WHERE name='addition.txt'")
        self.assertEqual(staff.request("/attachments/" + second)[0], 404)
        self.assertEqual(a.request(send, {**a.fields(url, url + "/save"), **{**data, "source_course": "Новая версия курса"}})[0], 303)
        self.assertEqual(self.post(staff, url, staff_action)[0], 303)
        self.assertEqual(self.post(staff, url, staff_action, note="Проверено 36 часов.")[0], 303)
        with closing(connect(self.path)) as db:
            db.execute("UPDATE service SET name='Имя из нового каталога' WHERE id='credit-transfer'")
        page = self.read_after_restart(a, url)
        self.assertIn("Рекомендация по перезачёту сохранена", page)
        self.assertIn("Уточни курс.", page)
        self.assertIn("Демо-курс", page)
        self.assertIn("Новая версия курса", page)
        self.assertNotIn("Имя из нового каталога", page)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM application_submission"), 2)
        self.assertEqual(b.request(url)[0], 404)
        self.assertIn("заявка: выполнена", a.request("/notifications")[2].lower())

    def test_attachment_limits_content_validation_and_idempotency(self):
        a = self.client("student-a")
        start = "/services/material-aid"
        url = a.request(start + "/draft", a.fields(start, start))[1]["Location"]
        self.assertEqual(self.upload(a, url, "file.pdf", b"demonstration")[0], 422)
        self.assertEqual(self.upload(a, url, "file.png", b"not a PNG")[0], 422)
        self.assertEqual(self.upload(a, url, "large.txt", b"a" * (2 * 1024 * 1024 + 1))[0], 413)
        png = PNG + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        png += png_chunk(b"tEXt", b"description\0synthetic") + png_chunk(b"IDAT", zlib.compress(b"\x00abc"))
        png += png_chunk(b"IEND", b"")
        fields = a.fields(url, url + "/attachments")
        self.assertEqual(self.upload(a, url, "example.png", png, fields=fields)[0], 303)
        self.assertEqual(self.upload(a, url, "example.png", png, fields=fields)[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM application_attachment"), 1)
        fid = self.db_one("SELECT id FROM application_attachment")
        status, response_headers, canonical = a.request("/attachments/" + fid)
        self.assertEqual(status, 200)
        self.assertTrue(response_headers["Content-Disposition"].startswith("attachment;"))
        self.assertNotIn(b"synthetic", canonical)
        for i in range(2):
            self.assertEqual(self.upload(a, url, f"extra-{i}.txt", b"example")[0], 303)
        self.assertEqual(self.upload(a, url, "extra-3.txt", b"example")[0], 422)

    def test_paper_delivery_and_scoped_denial(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        start = "/services/enrolment-certificate"
        response = self.post(a, start, start, purpose="Демо архив", delivery="paper", pickup="desk", copies="3")
        self.assertEqual(response[0], 303)
        url = response[1]["Location"]
        action = "/staff" + url
        self.assertEqual(self.post(staff, url, action)[0], 303)
        self.assertEqual(self.post(staff, url, action)[0], 303)
        self.assertEqual(self.post(a, url, url + "/receive")[0], 303)
        self.assertIn("ДЕМО · учебная часть А-104", a.request(url)[2])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM document"), 0)
        leave = "/services/academic-leave"
        fields = {"from_date": "2027-01-01", "to_date": "2027-01-12", "details": "Демонстрационная причина"}
        other = self.post(b, leave, leave, **fields)[1]["Location"]
        self.assertEqual(staff.request(other)[0], 404)
        self.assertEqual(self.db_one("SELECT group_id FROM account WHERE username='student-b'"), "demo-energy")
