from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4
from wsgiref.validate import validator

from dean import campus_pay
from dean.web import create_app

from .test_dean import Client, DemoFixture


class CampusPayTest(DemoFixture):
    def checkout(self, client, product):
        path = campus_pay.PATH
        payload = {**client.fields(path, path), "product": product}
        response = client.request(path, payload)
        self.assertEqual(response[0], 303, response[2])
        self.assertEqual(client.request(path, payload)[1]["Location"], response[1]["Location"])
        return response[1]["Location"]

    def confirm(self, client, path):
        action = path + "/confirm"
        return client.request(action, {**client.fields(path, action), "code": campus_pay.code(path.rsplit("/", 1)[1])})

    def test_terminal_link_after_sign_in_only_lists_owners_pending_purchases(self):
        from dean.access_paths import login_url

        with patch.dict("os.environ", {"DEAN_EXT_CAMPUS_PAY": "1", "DEAN_CAMPUS_LAB": "1"}):
            student, other, staff, guest = (Client(self.app) for _ in range(4))
            for client, login in ((student, "student-a"), (other, "student-b"), (staff, "staff")):
                self.assertEqual(client.login(login, self.passwords[login])[0], 303)
            url = campus_pay.TERMINAL
            start = login_url(url)
            self.assertEqual(guest.request(url)[1]["Location"], start)
            response = guest.request("/login", {
                **guest.fields(start, "/login"),
                "username": "student-a", "password": self.passwords["student-a"],
            })
            self.assertEqual(response[1]["Location"], url)
            _, _, setup = staff.request("/campus-lab/terminal")
            self.assertRegex(setup, r'data-terminal-url="http://[^"]+/campus-pay/terminal"')
            self.assertIn("QR учебной кассы", setup)
            first = self.checkout(student, "tea")
            second = self.checkout(other, "lunch")
            _, _, cash = student.request(url)
            self.assertIn(first + "/confirm", cash)
            self.assertNotIn(second, cash)
            self.assertEqual(student.request(first)[0], 200)
            self.assertEqual(self.db_one("SELECT count(*) FROM campus_demo_purchase WHERE state='paid'"), 0)
            action = first + "/confirm"
            self.assertEqual(student.request(action, student.fields(url, action))[0], 303)
            self.assertNotIn(first + "/confirm", student.request(url)[2])

    def test_qr_purchase_history_cancel_refund_and_other_wallet(self):
        a, b, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        path = campus_pay.PATH
        self.assertEqual(a.request(path)[0], 404)
        self.assertNotIn(campus_pay.LABEL, a.request("/all-services")[2])
        with patch.dict("os.environ", {"DEAN_EXT_CAMPUS_PAY": "1"}):
            self.assertIn(campus_pay.LABEL, a.request("/all-services")[2])
            self.assertEqual(staff.request(path)[0], 404)
            cart = self.checkout(a, "lunch")
            self.assertIn("QR демонстрационной оплаты", a.request(cart)[2])
            self.assertIn("300 демо-баллов", a.request(cart)[2])
            self.assertEqual(b.request(cart)[0], 404)
            action = cart + "/confirm"
            fields = a.fields(cart, action)
            self.assertEqual(a.request(action, {**fields, "code": "invalid"})[0], 422)
            self.assertEqual(self.db_one("SELECT count(*) FROM campus_demo_purchase WHERE state='paid'"), 0)
            settled = {**fields, "code": campus_pay.code(cart.rsplit("/", 1)[1])}
            self.assertEqual(a.request(action, settled)[0], 303)
            self.assertEqual(a.request(action, settled)[0], 303)
            self.assertEqual(a.request(action, {**settled, "request_id": uuid4().hex})[0], 303)
            self.assertIn("190 демо-баллов", a.request(cart)[2])
            self.assertIn("300 демо-баллов", b.request(path)[2])
            self.assertNotIn(cart, b.request(path)[2])
            a.app = validator(create_app(self.path))
            self.assertIn("190 демо-баллов", a.request(path)[2])
            self.assertIn("Подтверждена", a.request(path)[2])
            action = cart + "/cancel"
            refund = a.fields(cart, action)
            self.assertEqual(a.request(action, refund)[0], 303)
            self.assertEqual(a.request(action, refund)[0], 303)
            self.assertIn("Баллы возвращены", a.request(path)[2])
            self.assertIn("300 демо-баллов", a.request(path)[2])
            pending = self.checkout(a, "tea")
            cancelled = pending + "/cancel"
            self.assertEqual(a.request(cancelled, a.fields(pending, cancelled))[0], 303)
            self.assertIn("Отменена", a.request(path)[2])
        self.assertEqual(a.request(cart)[0], 404)

    def test_two_confirmations_cannot_overdraw_the_same_wallet(self):
        a = self.client("student-a")
        with patch.dict("os.environ", {"DEAN_EXT_CAMPUS_PAY": "1"}):
            first, second = (self.checkout(a, "kit") for _ in range(2))
            addresses = (first + "/confirm", second + "/confirm")
            entries = [{**a.fields(item, action), "code": campus_pay.code(item.rsplit("/", 1)[1])}
                       for item, action in zip((first, second), addresses)]
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda pair: a.request(*pair), zip(addresses, entries)))
            self.assertEqual(sorted(row[0] for row in outcomes), [303, 409])
            self.assertEqual(self.db_one("SELECT count(*) FROM campus_demo_purchase WHERE state='paid'"), 1)
            self.assertIn("85 демо-баллов", a.request(campus_pay.PATH)[2])
