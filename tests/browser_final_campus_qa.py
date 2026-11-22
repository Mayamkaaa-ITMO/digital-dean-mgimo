"""Repeat campus acceptance against an already running local demo server."""
import argparse
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def run(page, origin, width, height, evidence):
    page.set_viewport_size({"width": width, "height": height})
    base = origin + "/campus-map"
    page.goto(base)
    query = page.locator("#campus-query")
    assert 0 < query.bounding_box()["y"] < height
    page.get_by_role("link", name="Откуда Главный вход").click()
    origin_input = page.get_by_role("searchbox", name="Откуда идёшь?")
    expect(origin_input).to_be_focused()
    origin_input.fill("2047")
    expect(page.get_by_role("link", name="Выбрать старт", exact=False)).to_be_visible()
    origin_input.fill("1012")
    origin_input.press("Enter")
    expect(page.get_by_role("link", name="Откуда 1012")).to_be_visible()
    expect(query).to_be_focused()
    query.fill("2018-А")
    query.press("Enter")
    expect(page.locator("#campus-place")).to_be_focused()
    expect(page.locator("#campus-place h2")).to_have_text("Аудитория 2018а")
    page.screenshot(path=evidence / f"room-{width}.png")
    page.get_by_role("link", name="Маршрут сюда").click()
    expect(page.locator(".campus-endpoints")).to_have_text("1012 → 2018а")

    page.goto(base + "?room=a1-cafe")
    page.get_by_role("link", name="Откуда Главный вход").click()
    page.get_by_role("link", name="2-й этаж", exact=True).click()
    expect(page.get_by_role("searchbox", name="Откуда идёшь?")).to_be_visible()
    expect(page.get_by_role("link", name="Куда Кафе Монте-Кристо")).to_be_visible()
    page.locator('.campus-room[href*="from=a2-conference"] .campus-room-hit').click()
    expect(page.get_by_role("link", name="Куда Кафе Монте-Кристо")).to_be_visible()
    route_action = page.locator(".campus-search-route")
    expect(route_action).to_have_attribute("href", "/campus-map?from=a2-conference&to=a1-cafe#campus-plan")
    route_action.click()
    expect(page.locator(".campus-endpoints")).to_contain_text("→ Кафе Монте-Кристо")

    page.goto(base + "?from=v2-entry&to=n4-423")
    current = page.locator(".campus-current")
    current.locator(".campus-step-actions .campus-primary").click()
    expect(current.locator(".campus-context")).to_have_text("Шаг 2 из 4")
    share = page.locator("[data-campus-share]").get_attribute("data-campus-share")
    current.get_by_role("link", name="Предыдущий шаг").click()
    expect(current.locator(".campus-context")).to_have_text("Шаг 1 из 4")
    page.goto(origin + share)
    current.get_by_role("link", name="Обзор этажа").click()
    page.get_by_role("link", name="Продолжить маршрут").click()
    expect(current.locator(".campus-context")).to_have_text("Шаг 2 из 4")
    current.get_by_role("link", name="Изменить точки").click()
    for field, value in (("from", "1012"), ("to", "2018 a")):
        lookup = page.locator("#campus-point-" + field)
        lookup.fill(value)
        lookup.press("Enter")
    page.locator("#campus-route-fields button[type=submit]").click()
    expect(current.locator(".campus-endpoints")).to_have_text("1012 → 2018а")

    for key in ("v2", "v3", "v4", "b1", "b2", "b4", "a1", "a2", "g3", "n4"):
        page.goto(base + f"?building={key[0]}&floor={key[1]}")
        plan = page.locator(".campus-plan")
        viewport = page.locator("[data-campus-interactive]")
        viewport.wait_for()
        initial = plan.get_attribute("viewBox")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert plan.evaluate("""svg => {
          const b = svg.getBoundingClientRect();
          return [...svg.querySelectorAll('.campus-area')].every(el => {
            const r = el.getBoundingClientRect();
            return r.left >= b.left-1 && r.top >= b.top-1 && r.right <= b.right+1 && r.bottom <= b.bottom+1;
          });
        }""")
        viewport.press("+")
        enlarged = plan.get_attribute("viewBox")
        assert enlarged != initial
        viewport.press("ArrowRight")
        if enlarged == plan.get_attribute("viewBox"):
            viewport.press("ArrowLeft")
        assert enlarged != plan.get_attribute("viewBox")
        viewport.press("Home")
        expect(plan).to_have_attribute("viewBox", initial)

    page.goto(base + "?from=a1-cafe&to=a2-place-11")
    current.get_by_role("link", name="На 2-й этаж").click()
    expect(current.locator(".campus-context")).to_have_text("Шаг 2 из 2")
    expect(current).to_contain_text("Точное положение двери")
    page.locator("[data-campus-interactive]").wait_for()
    room = page.locator(".campus-room.campus-selected .campus-room-hit")
    assert room.evaluate("""hit => {
        const room = hit.getBoundingClientRect();
        const plan = hit.ownerSVGElement.getBoundingClientRect();
        return room.left >= plan.left-1 && room.right <= plan.right+1
            && room.top >= plan.top-1 && room.bottom <= plan.bottom+1;
    }""")
    page.screenshot(path=evidence / f"foyer-201k-{width}.png")
    page.goto(base + "?from=b2-place-36&to=b2-place-36")
    current.get_by_role("link", name="Обзор этажа").click()
    page.get_by_role("link", name="Продолжить маршрут").click()
    expect(current).to_contain_text("Ты уже в точке назначения")

    page.goto(origin + "/universities?q=кампус")
    page.get_by_role("link", name="ДЕМО-КАМПУС-01").click()
    page.locator('a[href*="room=b2-2018a"]').click()
    expect(page.locator("#campus-place h2")).to_have_text("Аудитория 2018а")
    page.goto(base + "?view=schedule")
    page.get_by_role("link", name="Посмотреть на плане").click()
    expect(page.locator("#campus-place")).to_contain_text("Точное положение двери")
    page.goto(base + "?from=n4-436&view=grounds&place=canteen")
    page.get_by_role("link", name="Начать маршрут").click()
    expect(current.locator(".campus-endpoints")).to_contain_text("столовой")
    assert "from=n4-436" in page.url
    page.goto(base + "?from=n4-436&to=n4-409")
    expect(current.locator(".campus-context")).to_have_text("Шаг 1 из 1")
    expect(page.locator(".campus-route-line")).to_be_visible()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:8068")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if urlsplit(args.origin).hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("Use a local demo origin.")
    args.output.mkdir(exist_ok=True, parents=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for size in ((320, 568), (360, 640), (390, 844), (768, 900), (1280, 800)):
            run(page, args.origin.rstrip("/"), *size, args.output)
            print(f"PASS {size[0]} × {size[1]}")
        assert not errors, errors
        browser.close()
