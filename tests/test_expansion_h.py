from datetime import datetime, timedelta
from uuid import uuid4

from dean.clock import at
from dean.fixtures import demo_id
from dean.finances import rubles
from dean.seed import UNIVERSITY

from .test_dean import DemoFixture


class FinanceProcessTest(DemoFixture):
    def post(self, client, page, action, **values):
        return client.request(action, {**client.fields(page, action), **values})

    def test_payment_failure_success_receipt_and_exact_totals(self):
        a, b = self.client("student-a"), self.client("student-b")
        cid = demo_id(UNIVERSITY, "finance", "student-a:charge:tuition")
        url = "/finance/charges/" + cid
        action = url + "/pay"
        pages = [a.request("/finance")[2], b.request("/finance")[2]]
        self.assertIn(rubles(1_230_045 + 80_025), pages[0])
        self.assertIn(rubles(980_010 + 71_050), pages[1])
        self.assertNotIn(rubles(1_230_045), pages[1])
        self.assertEqual(b.request(url)[0], 404)
        self.assertEqual(b.request(action, {**b.fields("/profile", "/logout"), "request_id": uuid4().hex,
                                            "result": "success"})[0], 404)
        failure = {**a.fields(url, action), "result": "failed"}
        self.assertEqual(a.request(action, failure)[0], 303)
        self.assertEqual(a.request(action, failure)[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM demo_payment WHERE state='failed'"), 1)
        success = {**a.fields(url, action), "result": "success"}
        self.assertEqual(a.request(action, success)[0], 303)
        self.assertEqual(a.request(action, success)[0], 303)
        self.assertEqual(a.request(action, {**success, "request_id": uuid4().hex})[0], 303)
        self.assertEqual(self.db_one("SELECT sum(amount) FROM demo_payment WHERE state='success'"), 1_230_045)
        pid = self.db_one("SELECT id FROM demo_payment WHERE state='success'")
        receipt = "/finance/receipts/" + pid
        self.assertEqual(b.request(receipt)[0], 404)
        self.assertTrue(a.request(receipt)[2].startswith(b"%PDF"))
        self.assertIn(rubles(80_025), a.request("/finance")[2])
        self.assertNotIn(rubles(1_230_045), a.request("/notifications")[2])
        self.assertEqual(self.post(a, "/finance", "/finance/visibility", private="")[0], 303)
        self.assertIn(rubles(1_230_045), a.request("/notifications")[2])
        self.assertNotIn(rubles(80_025), a.request("/")[2])
        self.assertIn("Оплачено в демо", self.read_after_restart(a, url))

    def test_support_decision_appointment_disbursement_and_private_housing(self):
        a, b, staff = (self.client(login) for login in ("student-a", "student-b", "staff"))
        portfolio = "/portfolio/new"
        created = self.post(a, portfolio, portfolio, kind="achievement", title="Демо-диплом",
                            content="Самостоятельный проект")
        self.assertEqual(created[0], 303)
        achievement = created[1]["Location"].rsplit("/", 1)[-1]
        service = "/services/material-aid"
        url = self.post(a, service, service, basis="study", details="Поддержка учебного проекта.",
                        achievement=achievement)[1]["Location"]
        action = "/staff" + url
        self.assertEqual(self.post(staff, url, action)[0], 303)
        form = {**staff.fields(url, action), "note": "Подтверждено по материалам."}
        self.assertEqual(staff.request(action, form)[0], 303)
        self.assertEqual(staff.request(action, form)[0], 303)
        award = self.db_one("SELECT id FROM finance_award WHERE source_app IS NOT NULL")
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM finance_disbursement WHERE award_id=?", (award,)), 0)
        self.assertIn("Демо-диплом", staff.request(url)[2])
        self.assertIn("Выплата ещё не отмечена", a.request("/finance/awards/" + award)[2])
        self.assertEqual(b.request("/finance/awards/" + award)[0], 404)
        staff_page = "/staff/finance/" + award
        self.assertEqual(self.post(staff, staff_page, staff_page + "/pay")[0], 303)
        self.assertEqual(self.db_one("SELECT amount FROM finance_disbursement WHERE award_id=?", (award,)), 250_000)
        second = self.post(a, service, service, basis="other", details="Повторный отдельный запрос.")[1]["Location"]
        second_staff = "/staff" + second
        self.assertEqual(self.post(staff, second, second_staff)[0], 303)
        self.assertEqual(self.post(staff, second, second_staff + "/refused", note="Нет основания.")[0], 303)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM finance_award WHERE source_app IS NOT NULL"), 1)
        self.assertIn("Д-204", a.request("/residence")[2])
        self.assertNotIn("Д-204", b.request("/residence")[2])
        stay = demo_id(UNIVERSITY, "finance", "student-a:stay")
        request_url = "/services/residence-request"
        self.assertEqual(self.post(b, request_url, request_url, residence=stay, issue="repair",
                                   details="Синтетическое обращение.")[0], 422)

    def test_budget_is_separate_and_reminder_is_single(self):
        a, b = self.client("student-a"), self.client("student-b")
        page = "/finance/budget"
        action = page + "/expense"
        reminder = (self.today + timedelta(days=1)).isoformat() + "T16:00"
        form = {**a.fields(page, action), "category": "study", "title": "Оплата демо-материалов",
                "amount": "12,34", "day": self.today.isoformat(), "reminder": reminder}
        self.assertEqual(a.request(action, {**form, "amount": "12,345"})[0], 422)
        self.assertEqual(a.request(action, form)[0], 303)
        self.assertEqual(a.request(action, form)[0], 303)
        self.assertEqual(self.db_one("SELECT sum(amount) FROM budget_entry"), 1234)
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM demo_payment"), 0)
        self.assertNotIn("Оплата демо-материалов", b.request(page)[2])
        due = datetime.fromisoformat(self.db_one("SELECT reminder_at FROM budget_entry"))
        with at(due):
            self.assertIn("Напоминание о записи бюджета", a.request("/notifications")[2])
            a.request("/notifications")
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM notification WHERE kind='budget'"), 1)
