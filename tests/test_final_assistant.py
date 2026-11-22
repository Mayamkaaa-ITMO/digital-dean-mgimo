import json
from contextlib import closing
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dean import assistant, auth, calendar_tools, workspace
from dean.ai import CHAT_MODEL
from dean.clock import at
from dean.db import connect
from dean.domain import identifier
from dean.seed import seed_id
from dean.web import create_app
from scripts.oauth_demo import DemoGateway
from scripts.prepare_final_demo import add_assistant_sample
from tests.test_assisted_dean import Model
from tests.test_dean import DemoFixture


class FinalAssistantTest(DemoFixture):
    def ask(self, client, question):
        fields = {**client.fields("/assistant?chat=new", "/assistant"), "message":question}
        status, _, body = client.request("/assistant", fields, HTTP_ACCEPT="application/json")
        self.assertEqual(status, 200, body)
        return json.loads(body)

    def test_study_answer_is_text_and_ignores_cabinet_actions(self):
        example = "У функции f(x) = x² производная равна 2x."
        engine = Model({"action":"explain_study", "answer":example, "navigate":"/staff", "link":"/grades"})
        self.app = create_app(self.path, ai_engine=engine)
        student = self.client("student-a")
        self.assertIn("Объясни производную на примере", student.request("/assistant")[2])
        reply = self.ask(student, "Что такое производная? Покажи на примере.")
        self.assertEqual(reply["text"], example)
        self.assertNotIn("navigate", reply)
        self.assertNotIn("link", reply)
        self.assertNotIn("proposal", reply)
        self.assertIn("explain_study", engine.calls[0][0][0]["content"])
        self.assertEqual(self.db_one("SELECT count(*) FROM assistant_proposal"), 0)

    def test_assistant_budget_allows_fifty_per_account_and_resets_next_day(self):
        model = Model(*({"action":"explain_study", "answer":"Объяснение"} for _ in range(52)))
        self.app = create_app(self.path, ai_engine=model)
        first, second = self.client("student-a"), self.client("student-b")
        moscow = ZoneInfo("Europe/Moscow")
        current = datetime.combine(self.today, datetime.min.time(), tzinfo=moscow) + timedelta(hours=23)
        question = "Объясни, как работает двоичный поиск"
        with at(current):
            for _ in range(50):
                self.assertEqual(self.ask(first, question)["text"], "Объяснение")
            fields = {**first.fields("/assistant?chat=new", "/assistant"), "message":question}
            status, _, body = first.request("/assistant", fields, HTTP_ACCEPT="application/json")
            self.assertEqual(status, 429)
            self.assertIn("50 запросов", json.loads(body)["error"])
            self.assertEqual(self.ask(second, question)["text"], "Объяснение")
            self.assertIn("задач", self.ask(first, "Мои задачи")["text"].casefold())
        with at(current + timedelta(hours=1)):
            self.assertEqual(self.ask(first, question)["text"], "Объяснение")
        self.assertEqual(len(model.calls), 52)

    def test_schedule_and_timed_task_answer_both_parts_without_model(self):
        engine = Model()
        self.app = create_app(self.path, ai_engine=engine)
        b = self.client("student-b")
        a = self.client("student-a")
        with closing(connect(self.path)) as db:
            first = auth.current_user(db, b.cookies["dean_session"])
            other = auth.current_user(db, a.cookies["dean_session"])
            day = self.today.isoformat()
            task = workspace.save_task(db, first, {"title": "Расчёт для лабораторной", "due": day + "T23:00"}, identifier())
            workspace.save_task(db, first, {"title": "Другая задача", "due": day + "T21:00"}, identifier())
            workspace.save_task(db, other, {"title": "Чужой расчёт", "due": day + "T23:00"}, identifier())
        reply = self.ask(b, "Какая у меня сегодня пара и что у меня запланировано к 23:00?")
        self.assertIn("Общая физика", reply["text"])
        self.assertIn("Расчёт для лабораторной", reply["text"])
        self.assertNotIn("Другая задача", reply["text"])
        self.assertNotIn("Чужой расчёт", reply["text"])
        self.assertEqual(len(reply["section_links"]), 2)
        self.assertEqual(reply["section_links"][1][0], "/tasks/" + task)
        self.assertFalse(engine.calls)

    def test_reported_schedule_date_and_audience_ignore_the_model_default(self):
        engine = Model(*({"action":"read_schedule", "period":"today"} for _ in range(4)))
        self.app = create_app(self.path, ai_engine=engine)
        client = self.client("student-a")
        with at(datetime(2026, 11, 22, 9, tzinfo=ZoneInfo("Europe/Moscow"))):
            for date_text in ("23 ноября", "23.11.2026", "2026-11-23"):
                with self.subTest(date=date_text):
                    reply = self.ask(client, f"Какие у меня пары {date_text} и где проходит первая?")
                    self.assertIn("date=2026-11-23&view=day", reply["link"])
            self.assertIn("Уточни дату", self.ask(client, "Какие пары 31 ноября?")["text"])
        self.assertFalse(engine.calls)

    def test_named_lesson_on_requested_date_uses_current_room_and_links_to_its_card(self):
        engine = Model({"action":"read_schedule", "period":"today"})
        self.app = create_app(self.path, ai_engine=engine)
        student, other, staff = (self.client(name) for name in ("student-a", "student-b", "staff"))
        tomorrow = self.today + timedelta(days=1)
        lesson = seed_id("a4")
        staff_path = f"/staff/schedule/{lesson}"
        for event, day, room in ((seed_id("a1"), self.today, "1012"),
                                 (lesson, tomorrow, "1012"), (lesson, tomorrow, "1015")):
            path = f"/staff/schedule/{event}"
            form = staff.fields(path, path)
            change = {**form, "room": room, "starts": f"{day}T09:00", "ends": f"{day}T10:30"}
            self.assertEqual(staff.request(path, change)[0], 303)
        name = "Алгоритмы и структуры данных"
        month = tuple(assistant.MONTHS)[tomorrow.month - 1]
        printed_date = f"{tomorrow.day} {month} {tomorrow.year} года"
        question = f"Во сколько занятие “{name}” {printed_date}, в какую аудиторию его перенесли и как открыть карточку?"
        day_query = calendar_tools.schedule_query(tomorrow, "day")
        card = f"/schedule/{lesson}?{day_query}"
        for wording in (question, f"В какую аудиторию перенесли пару «{name}» {tomorrow:%d.%m.%Y}?",
                        f'Во сколько занятие "{name}" завтра?'):
            with self.subTest(question=wording):
                reply = self.ask(student, wording)
                self.assertEqual(reply["text"], f"{tomorrow:%d.%m} 09:00 · {name} · 1015")
                self.assertEqual(reply["link"], card)
                self.assertEqual(reply["section_links"][0], [card, "Открыть карточку"])
                self.assertEqual(reply["section_links"][1][0], f"/schedule?{day_query}")
                self.assertEqual(student.request(card)[0], 200)
        self.assertEqual(self.ask(staff, question)["link"], staff_path)
        hidden = self.ask(other, question)
        self.assertIn("не найдено", hidden["text"])
        self.assertNotIn("1015", hidden["text"])
        self.assertFalse(engine.calls)

    def test_compound_question_uses_the_requested_day_for_personal_task(self):
        engine = Model()
        self.app = create_app(self.path, ai_engine=engine)
        student = self.client("student-a")
        target = (self.today + timedelta(days=1)).isoformat()
        with closing(connect(self.path)) as db:
            user = auth.current_user(db, student.cookies["dean_session"])
            workspace.save_task(db, user, {"title":"Подготовить лабораторную", "due":target + "T23:00"}, identifier())
            workspace.save_task(db, user, {"title":"Подготовить сегодня", "due":self.today.isoformat() + "T23:00"}, identifier())
        reply = self.ask(student, f"Какие у меня пары {target} и что сдать к 23:00?")
        self.assertIn("Иностранный язык", reply["text"])
        self.assertIn("Подготовить лабораторную", reply["text"])
        self.assertNotIn("Подготовить сегодня", reply["text"])
        self.assertIn(target, reply["link"])
        self.assertFalse(engine.calls)

    def test_compound_grade_and_exam_question_answers_both_with_grade_links(self):
        engine = Model({"action":"read_sections", "pages":["grades", "schedule"]})
        self.app = create_app(self.path, ai_engine=engine)
        student = self.client("student-a")
        question = "Какие у меня оценки и когда экзамен? Дай прямую ссылку на источник."
        reply = self.ask(student, question)
        self.assertIn("Успеваемость", reply["text"])
        self.assertIn("экзамен", reply["text"])
        self.assertTrue(any(href.startswith("/grades/") for href, _ in reply["section_links"]))
        with closing(connect(self.path)) as db:
            user = auth.current_user(db, student.cookies["dean_session"])
            db.execute("UPDATE learning_result SET exam_at=NULL WHERE student_id=?", (user["id"],))
        missing = self.ask(student, question)
        self.assertIn("Успеваемость", missing["text"])
        self.assertIn("даты экзаменов пока не указаны", missing["text"])
        self.assertFalse(engine.calls)

    def test_specific_grade_answer_adds_status_and_direct_result_and_retake_links(self):
        engine = Model()
        self.app = create_app(self.path, ai_engine=engine)
        student = self.client("student-b")
        with closing(connect(self.path)) as db:
            user = auth.current_user(db, student.cookies["dean_session"])
            result = db.execute("SELECT id FROM learning_result WHERE student_id=? AND subject_id='physics'",
                                (user["id"],)).fetchone()["id"]
            db.execute("INSERT INTO retake_debt (id,reason,conditions) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                       (result, "Не сдан экзамен", "Проверка преподавателем"))
        reply = self.ask(student, "Сколько баллов по общей физике, какой статус и что делать дальше?")
        self.assertIn("0 / 100", reply["text"])
        self.assertIn("Не сдано", reply["text"])
        self.assertIn("Ожидает допуска", reply["text"])
        self.assertEqual(reply["link"], "/grades/" + result)
        self.assertEqual(reply["section_links"][1][0], "/retakes/" + result)
        self.assertFalse(engine.calls)

    def test_confirmation_reopens_stale_form_without_repeating_effect(self):
        student = self.client("student-a")
        other = self.client("student-b")
        reply = self.ask(student, "Создай задачу Проверить форму Вишки")
        pid = reply["proposal"]
        path = f"/assistant/actions/{pid}/confirm"
        thread = "/assistant?chat=" + reply["chat"]
        old_form = student.fields(thread, path)
        student = self.client("student-a")
        self.assertEqual(student.request(path, old_form, HTTP_ORIGIN="https://example.invalid")[0], 403)
        expired, headers, _ = student.request(path, old_form)
        self.assertEqual((expired, headers["Location"]), (303, path))
        with closing(connect(self.path)) as db:
            db.execute("UPDATE assistant_proposal SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (pid,))
        self.assertEqual(other.request(path)[0], 404)
        self.assertIn("Проверить действие", student.request(thread)[2])
        self.assertIn("Выполнение ещё не подтверждено", student.request(path)[2])
        form = student.fields(path, path)
        skipped_review = student.request(path, {"csrf": form["csrf"]})
        self.assertEqual((skipped_review[0], skipped_review[1]["Location"]), (303, path))
        saved, headers, _ = student.request(path, form)
        self.assertEqual(saved, 303)
        location = headers["Location"]
        self.assertTrue(location.startswith("/tasks/"))
        self.assertIn("Действие выполнено", student.request(path)[2])
        self.assertEqual(student.request(path, form)[1]["Location"], location)
        with closing(connect(self.path)) as db:
            db.execute("UPDATE assistant_proposal SET result_path=NULL WHERE id=?", (pid,))
        recovery = student.request(path)[2]
        self.assertIn("Действие выполнено", recovery)
        self.assertIn(location, recovery)
        self.assertEqual(student.request(path, form)[1]["Location"], location)
        self.assertIn("Действие выполнено", student.request(thread)[2])
        self.assertEqual(self.db_one("SELECT COUNT(*) FROM task WHERE title=?", ("Проверить форму Вишки",)), 1)

    def test_finish_task_links_to_open_subtasks_and_rechecks_an_existing_proposal(self):
        student = self.client("student-a")
        title = "Подготовить стенд для защиты"
        question = f"Отметь задачу {title} выполненной"
        with closing(connect(self.path)) as db:
            user = auth.current_user(db, student.cookies["dean_session"])
            task = workspace.save_task(db, user, {"title": title}, identifier())
        original = self.ask(student, question)
        confirm = f'/assistant/actions/{original["proposal"]}/confirm'
        form = student.fields("/assistant?chat=" + original["chat"], confirm)
        with closing(connect(self.path)) as db:
            subtask = workspace.subtask_action(db, user, task, "add", None, "Проверить макет", identifier())
        card = "/tasks/" + task
        reply = self.ask(student, question)
        self.assertIn("открытые подзадачи", reply["text"])
        self.assertEqual(reply["section_links"], [[card, "Открыть задачу"]])
        self.assertNotIn("proposal", reply)
        blocked, _, html = student.request(confirm, form)
        self.assertEqual(blocked, 422)
        self.assertIn(f'href="{card}"', html)
        self.assertEqual(self.db_one("SELECT state FROM task WHERE id=?", (task,)), "todo")
        with closing(connect(self.path)) as db:
            workspace.subtask_action(db, user, task, "complete", subtask, "", identifier())
        saved, headers, _ = student.request(confirm, form)
        self.assertEqual((saved, headers["Location"]), (303, card))

    @patch.dict("os.environ", {"DEAN_OAUTH_CHANNEL":"candidate", "DEAN_AI_API_KEY":"synthetic"})
    def test_candidate_gateway_keeps_configured_model_after_provider_reload(self):
        from dean.access_config import AccessConfig
        with patch("scripts.oauth_demo.create_app") as factory:
            gateway = DemoGateway(self.path, "https://demo.example", self.path.with_suffix(".json"))
            gateway.application_for(AccessConfig())
            for call in factory.call_args_list:
                self.assertTrue(call.kwargs["ai_engine"].enabled)

    @patch.dict("os.environ", {"DEAN_EXT_CAMPUS_PAY":"1", "DEAN_EXT_ROOM_OCCUPANCY":"1",
                                "DEAN_EXT_CAMPUS_DIRECTORY":"1", "DEAN_EXT_LECTURE_STUDIO":"1"})
    def test_assistant_role_catalog_and_all_readers(self):
        for login in ("student-a", "student-b", "staff"):
            client = self.client(login)
            with closing(connect(self.path)) as db:
                user = auth.current_user(db, client.cookies["dean_session"])
                for name, (path, _, _) in assistant.pages(user, db).items():
                    with self.subTest(role=login, page=name):
                        self.assertEqual(client.request(urlsplit(path).path)[0], 200)
                for name in assistant.readable_sections(user):
                    with self.subTest(role=login, reader=name):
                        result = assistant.read_sections(db, user, {"pages":[name]}, identifier())
                        self.assertTrue(result["text"])
                        self.assertEqual(len(result["section_links"]), 1)

    def test_search_and_multi_section_reads_honor_actor(self):
        search = {"action":"search_cabinet", "query":"Зелёный маяк"}
        engine = Model(search, search, {"action":"read_sections", "pages":["finance", "documents", "https://other.test"]})
        self.app = create_app(self.path, ai_engine=engine)
        a, b, staff = [self.client(name) for name in ("student-a", "student-b", "staff")]
        with closing(connect(self.path)) as db:
            user = auth.current_user(db, a.cookies["dean_session"])
            tid = workspace.save_task(db, user, {"title":"Зелёный маяк"}, identifier())
        self.assertIn(["/tasks/" + tid, "Зелёный маяк"], self.ask(a, "Поищи зелёный маяк у меня")["section_links"])
        self.assertNotIn(tid, str(self.ask(b, "Поищи зелёный маяк у меня")))
        staff_reply = self.ask(staff, "Собери сводку с доступных мне страниц")
        self.assertEqual(staff_reply["section_links"], [["/staff/finance", "Назначения и выплаты"]])

    def test_grounded_curriculum_uses_published_credits_slots_and_eligibility(self):
        engine = Model({"action":"explain_catalog","page":"curriculum"}, {"answer":"Подойдут две дисциплины."})
        self.app = create_app(self.path, ai_engine=engine)
        response = self.ask(self.client("student-a"), "Как мне набрать нужные кредиты в блоке?")
        facts = json.loads(engine.calls[1][0][-1]["content"])["записи"]
        self.assertTrue(any("Командное проектирование" in row and "2 кр." in row and "мест" in row for row in facts))
        self.assertEqual([call[2] for call in engine.calls], [CHAT_MODEL, CHAT_MODEL])
        self.assertEqual(response["link"], "/curriculum")

    def test_material_answer_reads_only_logged_in_students_passages(self):
        request = {"action":"explain_material", "query":"импорту расписания"}
        with closing(connect(self.path)) as db:
            add_assistant_sample(db)
            add_assistant_sample(db)
            self.assertEqual(db.execute("SELECT count(*) FROM study_material WHERE title=?",
                                        ("Памятка по импорту расписания",)).fetchone()[0], 1)
        engine = Model(request, {"answer":"Сначала сверь время и аудиторию."}, request)
        self.app = create_app(self.path, ai_engine=engine)
        question = "Что нужно сверить по моей памятке по импорту расписания?"
        first = self.ask(self.client("student-a"), question)
        self.assertIn("/materials", first["link"])
        self.assertIn("Исправь ошибки в черновике", engine.calls[1][0][-1]["content"])
        self.assertIn("не нашлось", self.ask(self.client("student-b"), question)["text"])
        self.assertEqual(len(engine.calls), 3)

    def test_schedule_accepts_a_specific_future_day_and_next_week(self):
        target = self.today + timedelta(days=7)
        engine = Model({"action":"read_schedule", "period":"today"},
                       {"action":"read_schedule", "period":"next_week"})
        self.app = create_app(self.path, ai_engine=engine)
        client = self.client("student-a")
        self.assertIn(target.isoformat(), self.ask(client, "Какое число будет ровно через неделю от сегодня и какие пары в тот день?")["link"])
        monday = target - timedelta(days=target.weekday())
        self.assertIn(monday.isoformat(), self.ask(client, "А на следующей неделе?")["link"])

    def test_both_calendar_ranges_survive_model_selecting_only_one(self):
        self.app = create_app(self.path, ai_engine=Model({"action":"read_schedule", "period":"today"}))
        result = self.ask(self.client("student-a"), "Пары через неделю и на следующей календарной неделе?")
        urls = [link for link, label in result["section_links"]]
        target = self.today + timedelta(days=7)
        self.assertIn(target.isoformat(), urls[0])
        self.assertIn((target-timedelta(days=target.weekday())).isoformat(), urls[1])
        self.assertIn("view=week", urls[1])

    def test_calendar_is_available_at_account_limit_without_calling_provider(self):
        engine = Model()
        self.app = create_app(self.path, ai_engine=engine)
        with patch.dict("dean.ai.ACCOUNT_LIMITS", {"assistant": 0}):
            reply = self.ask(self.client("student-a"), "Какие пары через неделю от сегодня и на следующей неделе?")
        self.assertIn("лимит", reply["text"])
        self.assertIn("из календаря", reply["text"])
        self.assertEqual(len(reply["section_links"]), 2)
        self.assertIn((self.today + timedelta(days=7)).isoformat(), reply["link"])
        self.assertFalse(engine.calls)
