"""Single-database guest, student, organizer and second-student Chrome acceptance."""
import argparse
import json
import os
import re
from contextlib import closing
from datetime import datetime, time, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urlsplit
from wsgiref.simple_server import make_server

from playwright.sync_api import expect, sync_playwright

from dean import clock
from dean.__main__ import LocalServer
from dean.config import WebConfig
from dean.db import connect
from dean.seed import ZONE, credentials_path
from dean.web import create_app
from scripts.prepare_final_demo import populate
from tests.browser_assisted_qa import OfflineModel, PIXEL, REVIEW_LESSON, ask
from tests.browser_design_final_qa import skip_first_step
from tests.browser_seamless_qa import password_login
from tests.product_extensions_browser_server import QuietHandler


def post_form(page, action, *, button=None):
    form = page.locator(f'form[action="{action}"]')
    control = form.get_by_role("button", name=button, exact=True) if button else form.locator('button:not([formaction])').last
    control.click()
    page.wait_for_load_state("domcontentloaded")


def run(browser, width, out, results):
    with TemporaryDirectory(prefix="dean-final-journey-") as folder:
        path = Path(folder)/"demo.sqlite3"
        populate(path)
        passwords = json.loads(credentials_path(path).read_text())
        engine = OfflineModel()
        app = create_app(path, WebConfig(), ai_engine=engine)
        today = datetime.now(ZONE).date()
        instant = datetime.combine(today, time(9), ZONE)
        monday = today - timedelta(days=today.weekday())
        def fixture(env, respond):
            with clock.at(instant):
                return app(env, respond)
        with make_server("127.0.0.1", 0, fixture, server_class=LocalServer, handler_class=QuietHandler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"
            context = browser.new_context(viewport={"width":width,"height":800}, reduced_motion="reduce", timezone_id="Europe/Moscow")
            staff_context = browser.new_context(viewport={"width":width,"height":800}, reduced_motion="reduce", timezone_id="Europe/Moscow")
            student, employee = context.new_page(), staff_context.new_page()
            for page in (student,employee):
                page.set_default_timeout(6500)
            def mark(label):
                results.append({"width":width,"check":label,"status":"PASS"})
                print("PASS", width, label, flush=True)
            def go(page, route, status=200):
                response = page.goto(origin + route)
                assert response.status == status, (route,response.status)
            def signin(page, name):
                go(page,"/login")
                password_login(page,name,passwords[name])
                page.wait_for_url(origin+("/staff" if name=="staff" else "/"))
                skip_first_step(page)
            def logout(page):
                # Account menus vary by viewport; the common logout form is always in the header.
                page.locator('form[action="/logout"] button').filter(visible=True).first.click()
                page.wait_for_url(re.compile(r"/login"))
            def count(sql, *args):
                with closing(connect(path)) as db: return db.execute(sql,args).fetchone()[0]
            try:
                go(student,"/login")
                student.get_by_role("link",name="Посмотреть расписание без входа").click()
                student.get_by_role("link",name="ДЕМО-КАМПУС-01").click()
                student.locator('a[href*="room=b1-1012"]').click()
                expect(student.locator("#campus-place h2")).to_contain_text("1012")
                mark("Гость открывает расписание и карточку аудитории")
                signin(student,"student-a")
                lead = student.locator(".rw-lead:visible").first
                expect(lead).to_contain_text("1012")
                lead.click()
                lesson = urlsplit(student.url).path
                student.get_by_role("link",name="Построить маршрут").click()
                expect(student.locator(".campus-current")).to_contain_text("1012")
                student.get_by_role("link",name="К занятию",exact=True).click()
                assert urlsplit(student.url).path == lesson
                student.get_by_role("link",name="К расписанию",exact=True).first.click()
                mark("Ближайшая пара → аудитория → маршрут → та же пара → расписание")
                go(student,"/assistant")
                ask(student,"Какие пары завтра?")
                expect(student.locator("[data-chat-log]")).to_contain_text("Иностранный язык")
                ask(student,"Где находится моя следующая аудитория?")
                expect(student.locator('[data-chat-log] a[href*="room=b1-1012"]')).to_be_visible()
                chat_link=student.url
                task="Подготовиться к экзамену по линейной алгебре"
                ask(student,"Создай задачу "+task)
                proposal=student.locator(".dean-chat-confirmation").filter(has_text=task)
                expect(proposal).to_be_visible()
                assert not count("SELECT count(*) FROM task WHERE title=?",task)
                proposal.get_by_role("button",name="Подтвердить").click()
                expect(student.locator("main")).to_contain_text(task)
                assert count("SELECT count(*) FROM task WHERE title=?",task)==1
                mark("Ответ о завтрашних парах, карта из чата и подтверждение задачи")
                go(student,"/calendar/import/photo")
                student.locator('[name="week"]').fill(monday.isoformat())
                student.locator('[name="file"]').set_input_files({"name":"week.png","mimeType":"image/png","buffer":PIXEL})
                student.get_by_role("button",name="Распознать пары").click()
                row=student.locator(".dean-photo-row").filter(has_text=REVIEW_LESSON)
                expect(row).to_be_visible()
                draft=urlsplit(student.url).path
                row.locator("summary").click()
                row.locator('[name="room"]').fill("2018а")
                row.get_by_label("Сверил строку").check()
                row.get_by_role("button",name="Сохранить строку").click()
                student.get_by_role("button",name="Подтвердить личный импорт").click()
                schedule="/schedule?view=week&date="+monday.isoformat()
                go(student,schedule)
                expect(student.locator("main")).to_contain_text(REVIEW_LESSON)
                mark("Фото с тестовым ответом → исправленная строка → личная неделя")
                if width == 360:
                    periods = student.get_by_role("navigation", name="Период расписания")
                    for view, title in (("month", "Месяц"), ("day", "День"), ("list", "Список"), ("week", "Неделя")):
                        control = periods.get_by_role("link", name=title, exact=True)
                        expect(control).to_be_in_viewport()
                        assert control.bounding_box()["height"] >= 44
                        control.click()
                        expect(student.locator(".dean-schedule-feed")).to_have_attribute("data-view", view)
                        expect(control).to_have_attribute("aria-current", "page")
                    expect(student.get_by_role("link", name="Следующий период")).to_be_in_viewport()
                    mark("День, неделя, список и месяц переключаются с мобильной страницы расписания")
                go(student,"/activities")
                student.get_by_role("link",name="День студенческих проектов",exact=True).click()
                student.locator('a[href^="/slots/"]').first.click()
                ack=student.locator('[name="ack"]')
                if ack.count(): ack.check()
                student.get_by_role("button",name="Подтвердить запись").click()
                booking=urlsplit(student.url).path
                post_form(student,booking+"/ticket")
                expect(student.get_by_alt_text("QR-код билета")).to_be_visible()
                mark("Афиша → регистрация → личный QR-билет")
                service="/services/enrolment-certificate"
                go(student, service)
                student.locator('[name="purpose"]').fill("Для учебного проекта")
                post_form(student,service)
                student.wait_for_url(re.compile(r"/requests/[a-f0-9]{32}"))
                req=urlsplit(student.url).path
                signin(employee,"staff")
                go(employee,req)
                manage="/staff"+req
                post_form(employee,manage)
                form=employee.locator(f'form[action="{manage}/needs_changes"]')
                form.locator('[name="note"]').fill("Укажи название проекта.")
                form.get_by_role("button").click()
                go(student,"/notifications")
                student.locator(f'a[href="{req}"]:visible').first.click()
                expect(student.locator("main")).to_contain_text("Укажи название проекта.")
                editor=student.locator(f'form[action="{req}/save"]')
                editor.locator('[name="purpose"]').fill("Для проекта «Переговоры»")
                editor.get_by_role("button",name="Отправить повторно").click()
                student.wait_for_url(origin+req)
                go(employee,req)
                post_form(employee,manage)
                post_form(employee,manage)
                go(student,req)
                pdf=student.locator('a[href^="/documents/"][href*="download"]').first
                href=pdf.get_attribute('href')
                with student.expect_download() as downloaded: pdf.click()
                assert Path(downloaded.value.path()).read_bytes().startswith(b"%PDF-")
                mark("Заявка → замечание → уведомление → исправление → выдача PDF")
                logout(student)
                signin(student,"student-a")
                go(student,"/tasks")
                expect(student.locator("main")).to_contain_text(task)
                go(student,schedule)
                expect(student.locator("main")).to_contain_text(REVIEW_LESSON)
                go(student,"/documents")
                expect(student.locator('a[href^="/documents/"]').first).to_be_visible()
                go(student,"/assistant")
                if "?chat=" not in chat_link:
                    chat_id=count("SELECT id FROM assistant_conversation WHERE owner_id=(SELECT id FROM account WHERE username='student-a') ORDER BY updated_at DESC LIMIT 1")
                    chat_link="/assistant?chat="+chat_id
                else: chat_link=chat_link.removeprefix(origin)
                logout(student)
                signin(student,"student-b")
                for private in [req,href,draft,booking,chat_link]:
                    response=student.goto(origin+private)
                    assert response.status in (403,404), (private.split("?")[0],response.status)
                go(student,schedule)
                expect(student.locator("main")).not_to_contain_text(REVIEW_LESSON)
                mark("Повторный вход A и смена на B в одном браузере, изоляция пяти ссылок")
            except Exception:
                print("LAST PAGE",urlsplit(student.url).path,"STAFF",urlsplit(employee.url).path,flush=True)
                raise
            finally:
                context.close()
                staff_context.close()
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    results=[]
    with sync_playwright() as pw, closing(pw.chromium.launch(channel="chrome")) as browser:
        try:
            for width in (360,1280): run(browser,width,args.output,results)
        finally:
            (args.output/"journey.json").write_text(json.dumps(results,ensure_ascii=False,indent=2))
