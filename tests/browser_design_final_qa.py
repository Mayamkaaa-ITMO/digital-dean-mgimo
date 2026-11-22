"""Check the final branch in installed Chrome using only local demo data."""
import argparse
from contextlib import closing
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from tests.browser_assisted_qa import fixture, login


VIEWPORTS = ((320, 568), (360, 560), (360, 800), (390, 844), (768, 900),
             (1039, 800), (1040, 800), (1280, 800), (1440, 900))
PAGES = (
    "/schedule", "/campus-map", "/grades", "/work", "/documents", "/requests",
    "/activities", "/bookings", "/finance", "/tasks", "/profile", "/profile/access",
    "/profile/telegram", "/calendar/import/photo", "/calendar/share", "/library",
    "/practice", "/all-services", "/lecture-library", "/lecture-library/semantic-web/intro",
    "/lectures", "/campus-occupancy",
)


def screenshot(page, out, name):
    page.screenshot(path=out / (name + ".png"), animations="disabled")


def check_width(page):
    details = page.evaluate("""() => ({
        width: document.documentElement.clientWidth,
        scrollWidth: document.documentElement.scrollWidth,
        reduced: matchMedia('(prefers-reduced-motion: reduce)').matches
    })""")
    assert details["scrollWidth"] <= details["width"] + 1, (page.url, details)
    return details


def open_page(page, origin, path):
    response = page.goto(origin + path)
    assert response and response.status == 200, (path, response.status if response else None)
    page.wait_for_function("document.fonts.status === 'loaded'")
    expect(page.locator("#main h1")).to_be_attached()
    return check_width(page)


def skip_first_step(page):
    skip = page.get_by_role("button", name="Пропустить", exact=True)
    if skip.count():
        skip.click()
        expect(page.locator("[data-dean-overlay]")).to_have_count(0)


def prototype_theme(page):
    expect(page.locator("html")).to_have_attribute("data-theme", "raspay")
    expect(page.locator(".appearance-picker, input[name='appearance']")).to_have_count(0)
    expect(page.get_by_text("Светлый кабинет", exact=True)).to_have_count(0)
    assert "МГИМО" not in page.title().rsplit(" · ", 1)[-1]
    scheme, *background = page.evaluate("""() => [getComputedStyle(document.documentElement).colorScheme,
        ...getComputedStyle(document.body).backgroundColor.match(/[\\d.]+/g).slice(0, 3).map(Number)]""")
    assert "light" in scheme and min(background) >= 235, (page.url, scheme, background)


def layout(page, origin, width, height, out):
    page.set_viewport_size({"width": width, "height": height})
    result = open_page(page, origin, "/")
    result.update(width=width, height=height, theme="raspay")
    assert result["reduced"]
    prototype_theme(page)
    lead = page.locator(".rw-lead")
    expect(lead).to_contain_text("13:00")
    expect(lead).to_contain_text("А-108")
    box = lead.bounding_box()
    result["lessonBottom"] = round(box["y"] + box["height"], 1)
    desktop = page.locator(".dean-chat-embedded")
    launch = page.locator(".dean-header-assistant")
    if width >= 1040:
        expect(desktop).to_be_visible()
        expect(launch).to_be_visible()
        chat = desktop.bounding_box()
        main = page.locator(".dean-dashboard-content").bounding_box()
        assert chat["x"] > main["x"] + main["width"] and chat["y"] == main["y"], (width, chat, main)
        send = desktop.get_by_role("button", name="Отправить", exact=True).bounding_box()
        assert send["y"] + send["height"] <= height, (width, send, height)
        result["chatWidth"] = round(chat["width"])
        result["sendBottom"] = round(send["y"] + send["height"], 1)
    else:
        expect(desktop).to_be_visible()
        expect(launch).to_be_visible()
    if width in {360, 1039, 1040, 1280, 1440}:
        screenshot(page, out, f"today-{width}x{height}-raspay")
    return result


def menu(page):
    rail = page.locator("[data-dean-navigation]")
    toggle = page.locator("[data-dean-nav-toggle]")
    toggle.focus()
    page.keyboard.press("Enter")
    expect(rail).to_have_attribute("aria-modal", "true")
    expect(toggle).to_have_attribute("aria-expanded", "true")
    for path in ("/schedule", "/requests", "/documents", "/bookings", "/tasks", "/lecture-library", "/notifications", "/support"):
        expect(rail.locator(f'a[href="{path}"]')).to_be_visible()
    paths = rail.locator("nav a").evaluate_all("(links) => links.map(link => link.getAttribute('href'))")
    assert page.locator("main").evaluate("(node) => node.inert")
    for _ in range(13):
        page.keyboard.press("Tab")
        assert rail.evaluate("(node) => node.contains(document.activeElement)")
    page.keyboard.press("Escape")
    expect(toggle).to_be_focused()
    assert not page.locator("main").evaluate("(node) => node.inert")
    toggle.click()
    rail.get_by_role("link", name="Закрыть меню").click()
    expect(toggle).to_be_focused()
    toggle.click()
    page.set_viewport_size({"width": 1040, "height": 800})
    expect(rail).to_be_visible()
    expect(rail).not_to_have_attribute("aria-modal", "true")
    assert rail.locator("nav a").evaluate_all("(links) => links.map(link => link.getAttribute('href'))") == paths
    assert not page.locator("main").evaluate("(node) => node.inert")


def chat(page, context, origin, engine, out):
    page.set_viewport_size({"width": 360, "height": 800})
    open_page(page, origin, "/")
    page.locator(".dean-header-assistant").click()
    page.locator(".dean-chat-embedded").get_by_role("link", name="На весь экран").click()
    expect(page.locator(".dean-chat-page")).to_be_visible()
    textbox, log = page.get_by_role("textbox", name="Сообщение"), page.get_by_role("log", name="Сообщения")
    page.get_by_role("button", name="Расписание сегодня", exact=True).click()
    expect(log.get_by_role("link", name="Открыть расписание")).to_be_visible()
    first_path = urlsplit(page.url).path + "?" + urlsplit(page.url).query
    assert first_path.startswith("/assistant?chat=")
    page.reload()
    expect(log.get_by_role("link", name="Открыть расписание")).to_be_visible()
    page.locator("[data-chat-history] summary").click()
    expect(page.locator("[data-chat-thread-list] a[aria-current]")).to_have_text("Расписание сегодня")
    page.keyboard.press("Escape")
    expect(page.locator("[data-chat-history] summary")).to_be_focused()

    page.get_by_role("link", name="Новый чат").click()
    expect(log.locator("[data-chat-command]")).to_have_count(0)
    payload = "Как открыть расписание?\n" + "д" * 412
    real_complete = engine.complete
    answer = "Строка 1\nСтрока 2\nhttps://example.invalid/" + "x" * 280

    def model(messages, max_tokens, *, model):
        real_complete(messages, max_tokens, model=model)
        return {"action": "guide", "page": "schedule", "answer": answer}

    engine.complete = model
    cdp = context.new_cdp_session(page)
    cdp.send("Network.enable")
    throttle = {"offline": False, "latency": 320, "downloadThroughput": 180_000, "uploadThroughput": 120_000}
    cdp.send("Network.emulateNetworkConditions", throttle)
    try:
        textbox.fill(payload)
        page.get_by_role("button", name="Отправить", exact=True).click()
        expect(page.locator("[data-chat-status]")).to_contain_text("готовит ответ")
        expect(log.locator(".assistant-reply-text")).to_have_text(answer, use_inner_text=False)
    finally:
        cdp.send("Network.emulateNetworkConditions", {**throttle, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        engine.complete = real_complete
    check_width(page)
    assert log.evaluate("(node) => node.scrollWidth <= node.clientWidth + 1")
    textbox.focus()
    page.set_viewport_size({"width": 360, "height": 420})
    send = page.get_by_role("button", name="Отправить", exact=True)
    expect(send).to_be_in_viewport()
    assert log.evaluate("(el) => el.scrollHeight > el.clientHeight")
    screenshot(page, out, "assistant-keyboard-viewport-360x420")
    page.set_viewport_size({"width": 360, "height": 800})

    textbox.fill("Мои заявки")
    page.route("**/assistant", lambda route: (route.fetch(), route.abort()), times=1)
    page.get_by_role("button", name="Отправить", exact=True).click()
    expect(page.locator("[data-chat-status]")).to_contain_text("Сообщение осталось в поле.")
    expect(textbox).to_have_value("Мои заявки")
    screenshot(page, out, "assistant-retry-360")
    page.get_by_role("button", name="Повторить отправку").click()
    expect(log.get_by_role("link", name="Открыть заявки")).to_be_visible()
    expect(log.locator('[data-chat-speaker="user"]').filter(has_text="Мои заявки")).to_have_count(1)
    page.locator("[data-chat-history] summary").click()
    page.locator("[data-chat-thread-list]").get_by_role("link", name="Расписание сегодня", exact=True).click()
    assert page.url.endswith(first_path)
    expect(log.locator("[data-chat-command]")).to_have_count(1)

    textbox.fill("Создай задачу Подготовить доклад")
    page.get_by_role("button", name="Отправить", exact=True).click()
    confirmation = log.get_by_role("button", name="Подтвердить", exact=True)
    expect(confirmation).to_be_focused()
    screenshot(page, out, "assistant-conversation-360")
    confirmation.click()
    expect(page.locator("#main h1")).to_have_text("Подготовить доклад")
    open_page(page, origin, first_path)
    expect(log.get_by_text("Действие выполнено", exact=True)).to_be_visible()
    page.set_viewport_size({"width": 1280, "height": 800})
    open_page(page, origin, "/")
    expect(page.locator(".dean-chat-embedded").get_by_text("Действие выполнено")).to_be_visible()
    screenshot(page, out, "assistant-in-dashboard-1280")


def campus(page, origin, out):
    route = "/campus-map?from=n4-436&to=n4-409"
    page.set_viewport_size({"width": 360, "height": 560})
    open_page(page, origin, route + "&step=1")
    forward = page.get_by_role("link", name="Завершить маршрут", exact=True)
    expect(forward).to_be_in_viewport()
    step = forward.bounding_box()
    assert step["y"] + step["height"] <= 560, step
    screenshot(page, out, "map-step-360x560")
    for width in (360, 1280):
        page.set_viewport_size({"width": width, "height": 800})
        open_page(page, origin, route + "&step=1")
        page.get_by_role("button", name="Увеличить карту").click()
        screenshot(page, out, f"map-zoom-{width}")
        open_page(page, origin, route + "&progress=1&building=b&floor=1&room=b1-place-32")
        expect(page.locator("#campus-place")).to_contain_text("1045")
        expect(page.locator("#campus-place")).to_contain_text("Точное положение двери на плане не указано")
        resume = page.get_by_role("link", name="Продолжить маршрут", exact=True)
        resume.click()
        expect(forward).to_be_visible()
        forward.click()
        expect(page.locator("#campus-place")).to_contain_text("409")
    open_page(page, origin, "/campus-map?view=grounds&place=canteen")
    expect(page.locator("#campus-place")).to_contain_text("Студенческий зал на 2-м этаже")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    rows, routes = [], []
    with patch.dict("os.environ", {"DEAN_PRODUCT": "1", "DEAN_CAMPUS_LAB": "1", "DEAN_EXT_ROOM_OCCUPANCY": "1"}), \
        fixture() as (origin, passwords, _, engine, _), sync_playwright() as pw, closing(
        pw.chromium.launch(channel="chrome")
    ) as chrome:
        with closing(chrome.new_context(viewport={"width": 360, "height": 800}, timezone_id="Europe/Moscow",
                                       reduced_motion="reduce", has_touch=True)) as context:
            landing = context.new_page()
            for width in (360, 1280):
                landing.set_viewport_size({"width": width, "height": 800})
                open_page(landing, origin, "/login")
                landing.evaluate("localStorage.setItem('dean-appearance', 'light-office')")
                landing.reload()
                prototype_theme(landing)
                mascot = landing.locator("[data-vishka]")
                expect(mascot).to_be_visible()
                expect(landing.locator(".dean-login-photo")).to_have_count(0)
                landing.locator("#login-password").focus()
                expect(landing.locator("[data-vishka][data-private-pose]")).to_be_visible()
                landing.locator('input[name="username"]').focus()
                expect(landing.locator("[data-vishka][data-private-pose]")).to_have_count(0)
                expect(landing.get_by_role("button", name="Войти", exact=True)).to_be_visible()
                screenshot(landing, out, f"login-{width}")
            landing.close()
            page = login(context, origin, "student-a", passwords)
            skip_first_step(page)
            for width, height in VIEWPORTS:
                rows.append(layout(page, origin, width, height, out))
            page.set_viewport_size({"width": 360, "height": 800})
            menu(page)
            chat(page, context, origin, engine, out)
            campus(page, origin, out)
            for width in (360, 1280):
                page.set_viewport_size({"width": width, "height": 800})
                for path in PAGES:
                    open_page(page, origin, path)
                    prototype_theme(page)
                    routes.append({"path": path, "width": width, "theme": "raspay"})
                    if path == "/all-services":
                        expect(page.locator(".dean-service-board")).to_be_visible()
                        expect(page.locator(".dean-service-card img")).to_have_count(0)
                    if path in {"/all-services", "/lecture-library", "/lecture-library/semantic-web/intro", "/campus-occupancy"}:
                        screenshot(page, out, path.strip("/").replace("/", "-") + f"-{width}")
        summary = {"chrome": chrome.version, "layout": rows, "routes": routes,
                   "flows": ["more-menu","assistant-history","assistant-retry-after-server-write",
                             "assistant-action","network-latency-320ms","composer-360x420","campus-route-and-canteen"]}
        (out / "matrix.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
        print(f"PASS Chrome {chrome.version} · {len(rows)} проверок главной · {len(routes)} страниц · {len(summary['flows'])} сценариев")


if __name__ == "__main__":
    main()
