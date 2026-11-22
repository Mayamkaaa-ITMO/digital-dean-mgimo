"""Browser benchmark and loss-of-response checks on a disposable SQLite database."""

import argparse
import json
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlsplit
from wsgiref.simple_server import make_server

from playwright.sync_api import expect, sync_playwright

from dean import calendar_import, clock, public_schedule, public_sync
from dean.__main__ import LocalServer
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.fixtures import demo_id
from dean.seed import UNIVERSITY, credentials_path, seed, seed_id
from dean.web import create_app
from tests.browser_railway_qa import (
    GROUP, SERVICE, SOURCE, TIME, WIDTHS, QuietHandler, moved_lesson, navigation, open_page, seed_dense_day, submit,
)
from tests.browser_seamless_qa import full_width, password_login, skip_tour


CAPTURE = """() => {
  const read = () => Object.fromEntries([
    ['toolbar', '.rw-calendar-tools'], ['dates', '.rw-date-strip'],
    ['filters', '.rw-calendar-menu'], ['lesson', '.rw-calendar-day .lessons']
  ].map(([name, selector]) => {
    const box = document.querySelector(selector)?.getBoundingClientRect();
    return [name, box ? {height: box.height, top: box.top} : null];
  }));
  addEventListener('DOMContentLoaded', () => {
    const first = read();
    const status = document.fonts.status;
    window.auditGeometry = document.fonts.ready.then(() => new Promise(resolve => {
      requestAnimationFrame(() => resolve({first, ready: read(), fontsAtFirst: status}));
    }));
  }, {once: true});
}"""


@contextmanager
def fixture(*, dense=True):
    with TemporaryDirectory(prefix="dean-ux-audit-") as directory:
        db_path = Path(directory) / "qa.sqlite3"
        with clock.at(TIME), closing(connect(db_path)) as db:
            migrate(db)
            seed(db, db_path, anchor=TIME.date())
            if dense:
                seed_dense_day(db)
            for monday in ("2026-11-16", "2026-11-23"):
                week = public_schedule.week_scope(SOURCE, GROUP, monday)
                public_sync.publish(db, public_sync.stage_sample(db, week))
        passwords = json.loads(credentials_path(db_path).read_text())
        app = create_app(db_path, WebConfig(), access=AccessConfig())
        test_time = [TIME]

        def application(env, start_response):
            with clock.at(test_time[0]):
                return app(env, start_response)

        with make_server("127.0.0.1", 0, application, server_class=LocalServer, handler_class=QuietHandler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://localhost:{server.server_port}", passwords, db_path, test_time
            finally:
                server.shutdown()
                thread.join()


def opened(page, path):
    page.wait_for_url(f"**{path}")
    expect(page.locator("main h1")).to_be_visible()
    page.wait_for_load_state("load")
    return page.evaluate("""async () => {
      const geometry = await window.auditGeometry;
      const assets = performance.getEntriesByType('resource').filter(row => new URL(row.name).pathname.startsWith('/static/'));
      const fonts = assets.filter(row => new URL(row.name).pathname.endsWith('.woff2'));
      return {
        path: location.pathname,
        width: innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        fcpMs: performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? null,
        documentMs: performance.getEntriesByType('navigation')[0]?.responseStart ?? null,
        assets: assets.length,
        assetTransferBytes: assets.reduce((sum, row) => sum + row.transferSize, 0),
        fonts: fonts.map(row => ({path: new URL(row.name).pathname, responseEndMs: row.responseEnd,
                                  transferBytes: row.transferSize})),
        geometry
      };
    }""")


def measure(browser, origin, password, width, samples, baseline):
    records = []
    for network in ("ordinary", "slow"):
        for caching in ("cold", "warm"):
            with closing(browser.new_context(viewport={"width": width, "height": 900})) as context:
                page = context.new_page()
                context.add_init_script(f"({CAPTURE})();")
                page.goto(origin + "/login")
                password_login(page, "student-a", password)
                skip_tour(page)
                opened(page, "/")
                cdp = context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send("Network.setCacheDisabled", {"cacheDisabled": caching == "cold"})
                if network == "slow":
                    cdp.send("Network.emulateNetworkConditions", {
                        "offline": False, "latency": 700, "downloadThroughput": 90000,
                        "uploadThroughput": 45000, "connectionType": "cellular3g",
                    })
                for sample in range(samples):
                    page.goto(origin + "/")
                    route = [opened(page, "/")]
                    navigation(page, "/schedule")
                    route.append(opened(page, "/schedule"))
                    navigation(page, "/all-services")
                    route.append(opened(page, "/all-services"))
                    page.locator('main .rw-service-tiles a[href="/requests"]').click()
                    route.append(opened(page, "/requests"))
                    page.go_back()
                    route.append(opened(page, "/all-services"))
                    for index, row in enumerate(route):
                        row.update(network=network, cache=caching, sample=sample, step=index)
                        records.append(row)
                    if not baseline:
                        assert all(row["documentWidth"] == width for row in route), route
                        geometry = route[1]["geometry"]
                        assert geometry["first"]["toolbar"] == geometry["ready"]["toolbar"], geometry
                        if caching == "warm":
                            assert all(font["transferBytes"] == 0 for row in route for font in row["fonts"]), route
    return records


def recovery(browser, origin, password, width, db_path):
    with closing(browser.new_context(viewport={"width": width, "height": 800}, has_touch=width == 360)) as session:
        page = session.new_page()
        page.goto(origin + "/login")
        password_login(page, "student-a", password)
        page.goto(origin + SERVICE)
        purpose = page.locator('[name="purpose"]')
        send = page.get_by_role("button", name="Отправить заявку", exact=True)
        posts = []
        page.on("request", lambda request: posts.append(request.post_data)
                if request.method == "POST" and urlsplit(request.url).path == SERVICE else None)
        marker = f"Нет сети до отправки {width}"
        purpose.fill(marker)
        form_key = page.locator(f'form[action="{SERVICE}"] [name="request_id"]').first.input_value()
        session.set_offline(True)
        send.click()
        expect(page.get_by_role("alert")).to_contain_text("Связи нет")
        expect(purpose).to_have_value(marker)
        assert posts == [], posts
        session.set_offline(False)
        submit(page, "Отправить заявку")
        path = urlsplit(page.url).path
        assert parse_qs(posts[0])["request_id"] == [form_key]
        expect(page.locator("main article")).to_contain_text(marker)

        page.goto(origin + SERVICE)
        marker = f"Ответ потерян после записи {width}"
        purpose.fill(marker)
        posts.clear()
        committed = []

        def disconnect_after_commit(route):
            assert route.request.method == "POST"
            response = route.fetch()
            assert response.status == 200
            committed.append(response.json()["location"])
            route.abort("connectionreset")

        page.route(origin + SERVICE, disconnect_after_commit, times=1)
        send.click()
        expect(page.get_by_role("alert")).to_contain_text("Результат пока неизвестен")
        expect(purpose).to_have_value(marker)
        assert len(committed) == 1
        with closing(connect(db_path)) as db:
            assert db.execute("SELECT 1 FROM application WHERE id=?", (committed[0].rsplit("/", 1)[1],)).fetchone()
        with page.expect_response(lambda response: "receipt=" in urlsplit(response.url).query) as receipt:
            page.get_by_role("button", name="Повторить ту же попытку", exact=True).click()
        assert receipt.value.status == 200 and receipt.value.request.method == "GET"
        page.wait_for_url(origin + committed[0])
        expect(page.locator("main article")).to_contain_text(marker)
        page.goto(origin + "/requests")
        expect(page.locator("main .rows").get_by_text(marker, exact=True)).to_have_count(1)
        assert len(posts) == 1, posts
        return {"offlineBeforePost": path, "lostResponse": committed[0], "postsAfterLoss": len(posts)}


def nav_link(page, path):
    return page.locator(f'nav[aria-label="Основные разделы"] a:is([href="{path}"], [href^="{path}?"]):visible').first


def fast_navigation(browser, origin, password, width, *, graphite_v29=False):
    results = []
    for motion in ("no-preference", "reduce"):
        with closing(browser.new_context(viewport={"width": width, "height": 800}, reduced_motion=motion)) as context:
            page = context.new_page()
            page.goto(origin + "/login")
            password_login(page, "student-a", password)
            page.wait_for_url(origin + "/")
            skip_tour(page)
            if graphite_v29:
                picker = page.locator(".appearance-picker")
                picker.locator("summary").click()
                picker.get_by_role("radio", name="Railway v29 · Графит", exact=True).check()
                page.reload()
                expect(page.locator("html")).to_have_attribute("data-layout", "v29")
            for delay in (0, 50, 100, 150):
                page.goto(origin + "/all-services")
                nav_link(page, "/").click(no_wait_after=True)
                page.wait_for_url(origin + "/", wait_until="commit")
                hit = page.evaluate("""async (delay) => {
                  const selector = 'nav[aria-label="Основные разделы"] a:is([href="/schedule"], [href^="/schedule?"])';
                  let target;
                  do {
                    await new Promise(resolve => requestAnimationFrame(resolve));
                    target = [...document.querySelectorAll(selector)].find(link => link.getBoundingClientRect().width);
                  } while (!target);
                  const firstFrame = performance.now();
                  if (delay) await new Promise(resolve => setTimeout(resolve, delay));
                  const rect = target.getBoundingClientRect();
                  const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
                  const element = document.elementFromPoint(x, y);
                  return {x, y, elapsedMs: performance.now() - firstFrame, delay,
                          hit: element?.closest('a')?.getAttribute('href') || element?.tagName,
                          animation: getComputedStyle(document.getElementById('main')).animationName};
                }""", delay)
                assert urlsplit(hit["hit"]).path == "/schedule", (motion, width, hit)
                assert hit["animation"] == "none", hit
                page.mouse.click(hit["x"], hit["y"])
                page.wait_for_url(origin + hit["hit"], wait_until="domcontentloaded", timeout=3000)
                results.append({**hit, "motion": motion})
            page.goto(origin + "/all-services")
            for path in ("/", "/schedule"):
                target = nav_link(page, path)
                href = target.get_attribute("href")
                controls = page.locator('a[href]:visible, button:enabled:visible, input:enabled:visible, '
                                        'select:enabled:visible, textarea:enabled:visible, summary:visible, [tabindex="0"]:visible')
                for _ in range(controls.count() + 2):
                    page.keyboard.press("Tab")
                    if target.evaluate("node => node === document.activeElement"):
                        break
                expect(target).to_be_focused()
                page.keyboard.press("Enter")
                page.wait_for_url(origin + href)
            page.go_back()
            page.wait_for_url(origin + "/")
            expect(page.locator("#navigation-status")).to_be_hidden()
            if motion == "no-preference":
                page.evaluate("""() => {
                  sessionStorage.removeItem('qa-navigation-wait');
                  const status = document.getElementById('navigation-status');
                  new MutationObserver(() => {
                    if (!status.hidden && status.getBoundingClientRect().height > 0) {
                      sessionStorage.setItem('qa-navigation-wait', location.pathname);
                    }
                  }).observe(status, {attributeFilter: ['hidden']});
                }""")
                cdp = context.new_cdp_session(page)
                cdp.send("Network.enable")
                cdp.send("Network.emulateNetworkConditions", {
                    "offline": False, "latency": 700, "downloadThroughput": 90000,
                    "uploadThroughput": 45000, "connectionType": "cellular3g",
                })
                target = nav_link(page, "/schedule")
                href = target.get_attribute("href")
                target.click(no_wait_after=True)
                page.wait_for_url(origin + href)
                assert page.evaluate("sessionStorage.getItem('qa-navigation-wait')") == "/"
                expect(page.locator("#navigation-status")).to_be_hidden()
    return results


def import_csv(page, origin, width):
    main = open_page(page, origin, "/calendar/import", width)
    text = main.locator("[data-csv-input]")
    with page.expect_download() as download:
        main.get_by_role("link", name="Скачать шаблон CSV").click()
    assert download.value.path().read_text() == calendar_import.CSV_EXAMPLE
    main.get_by_role("button", name="Вставить пример").click()
    expect(text).to_have_value(calendar_import.CSV_EXAMPLE)
    bad = "неверный,заголовок\n" + calendar_import.CSV_EXAMPLE.split("\n", 1)[1]
    text.fill(bad)
    csv_form = main.locator('form[action="/calendar/import/csv"]').filter(has=page.locator("[data-csv-input]"))
    submit(page, "Проверить текст", inside=csv_form, status=422)
    error = main.locator("#calendar-csv-error")
    expect(error).to_be_focused()
    expect(error).to_contain_text("Строка 1.")
    expect(text).to_have_value(bad)
    page.keyboard.press("Tab")
    expect(text).to_be_focused()
    text.evaluate("node => node.setSelectionRange(0, node.value.indexOf('\\n'))")
    page.keyboard.insert_text(calendar_import.CSV_HEADER)
    expect(text).to_have_value(calendar_import.CSV_EXAMPLE)
    submit(page, "Проверить текст", inside=csv_form)
    expect(main.get_by_role("heading", name="Предпросмотр импорта")).to_be_visible()
    expect(main.get_by_text("Добавится событие", exact=True)).to_be_visible()
    full_width(page, width)

    main = open_page(page, origin, "/calendar/import", width)
    upload = main.locator('form[action="/calendar/import/csv"][enctype="multipart/form-data"]')
    picker = upload.locator('input[type="file"]')
    picker.set_input_files({"name": "calendar.csv", "mimeType": "text/csv", "buffer": bad.encode()})
    submit(page, "Загрузить для проверки", inside=upload, status=422)
    expect(main.locator("#calendar-csv-error")).to_be_focused()
    expect(upload.get_by_text("Исправь файл и выбери его снова.")).to_be_visible()
    expect(picker).to_have_value("")
    page.keyboard.press("Tab")
    expect(picker).to_be_focused()
    corrected = calendar_import.CSV_EXAMPLE.replace("2026-11-20", "2026-11-24")
    picker.set_input_files({"name": "calendar.csv", "mimeType": "text/csv", "buffer": corrected.encode()})
    submit(page, "Загрузить для проверки", inside=upload)
    expect(main.get_by_text("Добавится событие", exact=True)).to_be_visible()
    submit(page, "Подтвердить личный импорт")
    expect(main.get_by_text("Изменения применены к твоему календарю.", exact=True)).to_be_visible()


def retake_states(student, staff, other, origin, width, db_path, test_time):
    debt = demo_id(UNIVERSITY, "retake", "previous-practicum")
    path, manage = "/retakes/" + debt, "/staff/retakes/" + debt
    first, later = ("/slots/" + demo_id(UNIVERSITY, "retake", f"slot-{offset}") for offset in (2, 5))
    open_page(student, origin, path, width).locator(f'a[href="{first}"]').click()
    main = student.locator("main")
    expect(main.get_by_text("Ожидает допуска", exact=True)).to_be_visible()
    expect(main.get_by_text("Проверка отчёта и допуск учебной части.", exact=True)).to_be_visible()
    expect(main.get_by_role("button", name="Подтвердить запись")).to_have_count(0)
    open_page(other, origin, first, width, status=404)
    form = open_page(staff, origin, manage, width).locator(f'form[action="{manage}/admit"]')
    form.locator("textarea").fill("Отчёт проверен.")
    submit(staff, "Подтвердить допуск", inside=form)
    button = main.get_by_role("button", name="Подтвердить запись")
    student.reload()
    expect(button).to_be_visible()
    expect(main.get_by_text("Можно записаться.", exact=True)).to_be_visible()

    with closing(connect(db_path)) as db:
        b_debt = demo_id(UNIVERSITY, "retake", "qa-rival")
        db.execute("UPDATE account SET group_id='demo-ai' WHERE username='student-b'")
        db.execute("""
            INSERT INTO learning_result(id,university_id,student_id,period_id,subject_id,kind,outcome)
            VALUES(?,?,?,'demo-previous','demo-practicum','credit','failed')
        """, (b_debt, UNIVERSITY, seed_id("student-b")))
        db.execute("INSERT INTO retake_debt(id,reason,conditions,admitted) VALUES(?,?,?,1)",
                   (b_debt, "Тестовая задолженность", "Отчёт проверен."))
    open_page(other, origin, first, width)
    submit(other, "Подтвердить запись")
    student.reload()
    expect(main.get_by_text("Свободных мест нет. Выбери другое окно.", exact=True)).to_be_visible()
    expect(button).to_have_count(0)
    cancel = other.locator('main form[action$="/cancel"]')
    with other.expect_response(lambda response: response.request.method == "POST") as response:
        cancel.locator("button").click()
    assert response.value.status == 303
    student.reload()
    submit(student, "Подтвердить запись")
    rid = urlsplit(student.url).path.rsplit("/", 1)[1]
    with closing(connect(db_path)) as db:
        end = db.execute("SELECT ends_at FROM reservation WHERE id=?", (rid,)).fetchone()[0]
    test_time[0] = datetime.fromisoformat(end)
    result = open_page(staff, origin, manage, width).locator(f'form[action="{manage}/result"]')
    result.locator('[name="result"]').select_option("passed")
    result.locator("textarea").fill("Пересдача завершена.")
    submit(staff, "Зафиксировать результат", inside=result)
    expect(open_page(student, origin, later, width).get_by_text("Задолженность закрыта", exact=True)).to_be_visible()
    expect(button).to_have_count(0)


def controls(browser, origin, passwords, width, db_path, test_time):
    pages = []
    with closing(browser.new_context(viewport={"width": width, "height": 800})) as guest:
        params = urlencode({"source": SOURCE, "group": GROUP})
        main = open_page(guest.new_page(), origin, "/universities/group?" + params, width)
        expect(main.get_by_text("Неделя с 16 ноября 2026", exact=False).first).to_be_visible()
    try:
        for role in ("student-a", "staff", "student-b"):
            context = browser.new_context(viewport={"width": width, "height": 800})
            page = context.new_page()
            pages.append(page)
            page.goto(origin + "/login")
            password_login(page, role, passwords[role])
            skip_tour(page)
        student, staff, other = pages
        params = {"date": TIME.date().isoformat(), "view": "week", "kind": "lesson",
                  "subject": "algorithms", "visibility": "study"}
        path = "/schedule?" + urlencode(params)
        lesson = "/schedule/" + seed_id("a1")
        main = open_page(student, origin, path, width)
        main.locator(f'a[href^="{lesson}?"]').first.click()
        main.get_by_role("link", name="К расписанию", exact=True).click()
        expect(main.locator(".rw-calendar-tools")).to_be_visible()
        assert parse_qs(urlsplit(student.url).query) == {name: [value] for name, value in params.items()}
        filters = main.locator(".rw-active-filters")
        expect(filters.locator(".tag")).to_have_count(3)
        expect(filters.get_by_text("Занятия", exact=True)).to_be_visible()
        expect(filters.get_by_text("Только учебные события", exact=True)).to_be_visible()
        filters.get_by_role("link", name="Показать всё").click()
        expect(filters).to_have_count(0)
        assert parse_qs(urlsplit(student.url).query, keep_blank_values=True) == {
            "date": [params["date"]], "view": ["week"], "kind": [""], "subject": [""], "visibility": ["all"],
        }
        moved_lesson(student, staff, origin, width)
        import_csv(student, origin, width)
        retake_states(student, staff, other, origin, width, db_path, test_time)
    finally:
        for page in pages:
            page.context.close()


def summaries(rows):
    out = []
    for key in sorted({(r["width"], r["network"], r["cache"], r["step"], r["path"]) for r in rows}):
        group = [r for r in rows if (r["width"], r["network"], r["cache"], r["step"], r["path"]) == key]
        fcp = [r["fcpMs"] for r in group if r["fcpMs"] is not None]
        out.append({
            "width": key[0], "network": key[1], "cache": key[2], "step": key[3], "path": key[4],
            "medianFcpMs": median(fcp) if fcp else None, "worstFcpMs": max(fcp, default=None),
            "medianAssetBytes": median([r["assetTransferBytes"] for r in group]),
            "medianFontBytes": median([sum(font["transferBytes"] for font in r["fonts"]) for r in group]),
        })
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="Measure the unmodified release.")
    parser.add_argument("--retest-only", action="store_true", help="Run the acceptance scenarios without performance samples.")
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    if args.baseline and args.retest_only:
        parser.error("--baseline measures the original release without retest assertions")
    rows, retest = [], []
    with sync_playwright() as p, closing(p.chromium.launch(channel="chrome", chromium_sandbox=True)) as browser:
        for width in WIDTHS:
            if not args.retest_only:
                with fixture() as (origin, passwords, _, _):
                    rows.extend(measure(browser, origin, passwords["student-a"], width, args.samples, args.baseline))
            if not args.baseline:
                with fixture(dense=False) as (origin, passwords, db_path, test_time):
                    nav = fast_navigation(browser, origin, passwords["student-a"], width)
                    nav_v29 = fast_navigation(browser, origin, passwords["student-a"], width, graphite_v29=True)
                    loss = recovery(browser, origin, passwords["student-a"], width, db_path)
                    controls(browser, origin, passwords, width, db_path, test_time)
                    retest.append({"width": width, "navigation": nav, "navigationV29": nav_v29,
                                   "recovery": loss, "controls": "PASS"})
        result = {"chrome": browser.version, "baseline": args.baseline, "retest": retest,
                  "summary": summaries(rows), "samples": rows}
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
