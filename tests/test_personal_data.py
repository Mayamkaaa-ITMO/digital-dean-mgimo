from dataclasses import replace
from urllib.parse import urlencode
from unittest.mock import patch

from dean import personal_data, web
from tests.test_dean import DemoFixture

CONSENT = personal_data.CONSENT
POLICY = personal_data.POLICY
WITHDRAW = personal_data.WITHDRAW
SETTINGS = personal_data.Settings(
    required=True, name="Тестовый оператор", address="Тестовый адрес", email="privacy@example.test",
    retention="На время теста.", processors="Тестовые исполнители.",
    auth_basis="Основание тестовой авторизации.", transfer="В тесте отсутствует.",
)


class PersonalDataTest(DemoFixture):
    def configure(self, settings):
        with patch.object(personal_data.Settings, "from_env", return_value=settings):
            self.app = web.create_app(self.path)

    def test_separate_grant_revocation_returns_to_consent_with_history(self):
        self.configure(SETTINGS)
        guest = self.client()
        self.assertIn('href="/privacy"', guest.request("/login")[2])
        self.assertIn(SETTINGS.name, guest.request(POLICY)[2])
        self.assertNotIn('name="agree"', guest.request(CONSENT)[2])
        self.assertEqual(guest.request(CONSENT, {"agree": "1"})[0], 403)
        student = self.client("student-a")
        other = self.client("student-b")
        target = "/schedule?view=month"
        gate = CONSENT + "?" + urlencode({"next": target})
        self.assertEqual(student.request(target)[1]["Location"], gate)
        self.assertEqual(student.request("/tasks/new", {"title": "New task"})[0], 403)
        self.assertEqual(student.request("/universities")[0], 200)
        fields = student.fields(gate, CONSENT)
        self.assertEqual(student.request(CONSENT, fields)[0], 422)
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1", "csrf": "no"})[0], 403)
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1", "version": "old"})[0], 409)
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1"})[1]["Location"], target)
        self.assertEqual(student.request(target)[0], 200)
        self.assertNotEqual(other.request(target)[0], 200)
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1"})[0], 303)
        self.assertEqual(self.db_one("SELECT count(*) FROM privacy_consent_event"), 1)
        self.assertEqual(self.db_one("SELECT consent_text FROM privacy_consent_document"),
                         "\n".join(SETTINGS.consent_text))
        withdrawal = student.fields(CONSENT, WITHDRAW)
        self.assertEqual(student.request(WITHDRAW, withdrawal)[1]["Location"], CONSENT)
        self.assertEqual(student.request(target)[1]["Location"], gate)
        self.assertEqual(self.db_one("SELECT action FROM privacy_consent_event ORDER BY id DESC"), "withdraw")
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1"})[0], 409)
        self.assertIn("Согласие отозвано", student.request(CONSENT)[2])
        self.assertEqual(self.db_one("SELECT count(*) FROM privacy_consent_event"), 2)

    def test_new_text_requires_new_grant_with_existing_session(self):
        self.configure(SETTINGS)
        student = self.client("student-a")
        fields = student.fields(CONSENT, CONSENT)
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1"})[0], 303)
        self.configure(replace(SETTINGS, retention="Обновлённый срок хранения."))
        student.app = self.client().app
        self.assertTrue(student.request("/")[1]["Location"].startswith(CONSENT))
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1"})[0], 409)
        fresh = student.fields(CONSENT, CONSENT)
        self.assertEqual(student.request(CONSENT, fresh | {"agree": "1", "next": "//external.test"})[1]["Location"], "/")
        self.assertEqual(student.request("/")[0], 200)
        self.assertEqual(self.db_one("SELECT count(*) FROM privacy_consent_document"), 2)

    def test_demo_without_operator_does_not_record_consent_or_block(self):
        self.configure(personal_data.Settings())
        student = self.client("student-a")
        self.assertEqual(student.request("/")[0], 200)
        self.assertIn("Реквизиты оператора", student.request(POLICY)[2])
        self.assertNotIn('name="agree"', student.request(CONSENT)[2])
        fields = student.fields("/profile", "/logout")
        self.assertEqual(student.request(CONSENT, fields | {"agree": "1"})[0], 403)
        self.assertEqual(self.db_one("SELECT count(*) FROM privacy_consent_event"), 0)

    def test_required_mode_needs_real_configuration(self):
        with patch.dict("os.environ", {"DEAN_PRIVACY_REQUIRE_CONSENT": "1"}, clear=True):
            with self.assertRaisesRegex(ValueError, "DEAN_PRIVACY_"):
                personal_data.Settings.from_env()
        env = {
            "DEAN_PRIVACY_REQUIRE_CONSENT": "1", "DEAN_PRIVACY_OPERATOR": SETTINGS.name,
            "DEAN_PRIVACY_ADDRESS": SETTINGS.address, "DEAN_PRIVACY_EMAIL": SETTINGS.email,
            "DEAN_PRIVACY_RETENTION": SETTINGS.retention, "DEAN_PRIVACY_PROCESSORS": SETTINGS.processors,
            "DEAN_PRIVACY_AUTH_BASIS": SETTINGS.auth_basis, "DEAN_PRIVACY_TRANSFER": SETTINGS.transfer,
        }
        with patch.dict("os.environ", env, clear=True):
            self.assertEqual(personal_data.Settings.from_env(), SETTINGS)
