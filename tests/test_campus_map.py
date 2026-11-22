from contextlib import closing
from html import escape, unescape
import json
import os
import re
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from dean import activity_extensions, campus_map
from dean.campus_map_view import display_labels, drawing_path
from dean.db import connect, write
from dean.seed import seed_id
from dean.static_assets import asset_url
from tests.test_dean import DemoFixture


class CampusRoutingTest(TestCase):
    def test_named_routes_change_buildings_and_floors_at_mapped_landings(self):
        data = campus_map.campus()
        from_entry = lambda target: campus_map.stages(campus_map.route(data, campus_map.ENTRY, target))
        self.assertEqual([leg[0].floor for leg in from_entry("a2-conference")], ["v2","b2","b1","a1","a2"])
        steps = from_entry("n4-423")
        self.assertEqual([leg[0].floor for leg in steps], ["v2","v3","g3","n4"])
        self.assertEqual((steps[2][-1].key, steps[3][0].key), ("g3-n","n4-g"))
        backwards = campus_map.stages(campus_map.route(data, "n4-423", campus_map.ENTRY))
        self.assertEqual([leg[0].floor for leg in backwards], ["n4","g3","v3","v2"])
        self.assertEqual([leg[0].floor for leg in from_entry("b4-4017")], ["v2","v3","v4","b4"])

    def test_search_suffix_and_reachable_points(self):
        data = campus_map.campus()
        self.assertEqual(campus_map.find(data, "ауд. 2018a")[0].key, "b2-2018a")
        self.assertEqual(campus_map.find(data, "423")[0].key, "n4-423")
        self.assertEqual(campus_map.find(data, "зал №4")[0].key, "v4-hall-4")
        self.assertEqual(campus_map.find(data, "00000"), [])
        self.assertEqual(campus_map.find(data, "2047")[0].kind, "location")
        self.assertGreater(len(list(data.directory())), len(list(data.points())))
        for point in data.points():
            with self.subTest(point=point.label):
                self.assertTrue(campus_map.route(data, campus_map.ENTRY, point.key))

    def test_traced_walls_have_smooth_segments_and_keep_closed_rings(self):
        floor = campus_map.campus().floors["n4"]
        walls = floor.walls
        display = drawing_path(walls)
        self.assertEqual(display.count("M"), walls.count("M"))
        self.assertEqual(display.count("Z"), walls.count("Z"))
        self.assertIn("Q", display)
        self.assertIn("M264 40H234V94H264Z", display)
        self.assertLess(display.count("H") + display.count("V"), (walls.count("H") + walls.count("V")) / 2)
        self.assertEqual(floor.walls, walls)
        native = "M0 0H40Q50 5 50 10V25H0Z"
        self.assertEqual(drawing_path(native), native)

    def test_display_matches_room_numbers_to_their_own_outline(self):
        data = campus_map.campus()
        for floor_key, expected in (("v4", {"4004": 12, "4102": 23, "4102а": 27}),
                                    ("g3", {"3121": 13, "3122": 50, "3124": 2, "3137": 23})):
            floor = data.floors[floor_key]
            layout = {data.places[key].label: area for key, _, _, area in display_labels(data, floor)}
            self.assertEqual({name: layout[name] for name in expected}, expected)
        self.assertEqual(campus_map.find(data, "профком")[0].key, "a2-208k")


class CampusMapTest(DemoFixture):
    def test_mobile_map_exit_keeps_role_and_lesson_context(self):
        for login, target in ((None, "/guest"), ("student-a", "/all-services"),
                              ("student-b", "/all-services"), ("staff", "/staff")):
            with self.subTest(login=login):
                client = self.client(login)
                status, _, body = client.request("/campus-map?from=v2-entry&to=b2-2018a")
                self.assertEqual(status, 200)
                self.assertIn(f'href="{target}" aria-label="Выйти из карты"', body)
                self.assertEqual(client.request(target)[0], 200)
        staff = self.client("staff")
        lesson = re.search(r'href="(/staff/schedule/[a-f0-9]{32})"', staff.request("/staff")[2])[1]
        path = "/campus-map?" + urlencode({"room": "b2-2018a", "back": lesson})
        self.assertIn(f'href="{lesson}" aria-label="Выйти из карты"', staff.request(path)[2])
        self.assertEqual(staff.request(lesson)[0], 200)

    def test_lesson_map_returns_to_the_original_week_and_filters(self):
        eid = seed_id("a1")
        with closing(connect(self.path)) as db, write(db):
            db.execute("UPDATE schedule_event SET room=? WHERE id=?", (activity_extensions.rooms()["b1-1012"], eid))
        student = self.client("student-a")
        filters = {"date": self.today.isoformat(), "view": "week", "kind": "lesson",
                   "subject": "algorithms", "visibility": "study"}
        query = urlencode(filters)
        lesson = f"/schedule/{eid}?{query}"
        calendar = student.request(f"/schedule?{query}")[2]
        preview = unescape(re.search(rf'<a href="{re.escape(escape(lesson))}"[^>]+data-room="([^"]+)"', calendar)[1])
        self.assertEqual(parse_qs(urlsplit(preview).query), {"room": ["b1-1012"], "back": [lesson]})
        detail = student.request(lesson)[2]
        direct = unescape(re.search(r'href="(/campus-map\?[^"]+)"[^>]*>Построить маршрут', detail)[1])
        location = student.request(preview)[2]
        route = unescape(re.search(r'href="(/campus-map\?[^"]+)"[^>]*>Маршрут сюда', location)[1])
        next_step = route.replace("#campus-plan", "&step=2#campus-plan")
        for path in (direct, route, next_step):
            with self.subTest(path=path):
                status, _, body = student.request(path)
                self.assertEqual(status, 200)
                self.assertIn(f'href="{escape(lesson)}" aria-label="Выйти из карты"', body)
        self.assertIn(f'href="{escape("/schedule?" + query)}"', student.request(lesson)[2])
        public = self.client().request(route)[2]
        self.assertIn('href="/guest" aria-label="Выйти из карты"', public)
        unsafe = "/campus-map?" + urlencode({"to": "b1-1012", "back": "//example.com" + lesson})
        self.assertIn('href="/all-services" aria-label="Выйти из карты"', student.request(unsafe)[2])

    @patch.dict(os.environ, {"DEAN_EXT_ROOM_OCCUPANCY": "1"})
    def test_map_exit_returns_to_staff_occupancy_period(self):
        staff = self.client("staff")
        origin = "/campus-occupancy?period=week"
        status, _, occupancy = staff.request(origin)
        self.assertEqual(status, 200)
        route = unescape(re.search(r'href="(/campus-map\?[^"]+)"[^>]*>Маршрут на карте', occupancy)[1])
        self.assertEqual(parse_qs(urlsplit(route).query)["back"], [origin])
        self.assertIn(f'href="{origin}" aria-label="Выйти из карты"', staff.request(route)[2])
        self.assertIn('href="/guest" aria-label="Выйти из карты"', self.client().request(route)[2])

    def test_map_is_public_and_reached_from_guest_and_student_navigation(self):
        guest, student = self.client(), self.client("student-a")
        for client, source in ((guest, "/guest"), (student, "/all-services")):
            self.assertIn('href="/campus-map"', client.request(source)[2])
            status, _, body = client.request("/campus-map")
            self.assertEqual(status, 200)
            self.assertIn("Карта кампуса", body)
            self.assertIn("Корпус В · 2-й этаж", body)
            self.assertIn("Официальный план этажа", body)
            self.assertIn('class="campus-plan-canvas"', body)
            self.assertIn('class="campus-walls"', body)
            self.assertIn('class="campus-area campus-area-room"', body)
            self.assertIn(asset_url("campus-map.css"), body)
        self.assertEqual(guest.request(asset_url("campus-map.css"))[0], 200)
        self.assertEqual(guest.request("/campus-map", {"from":"v2-entry","to":"n4-423"})[0], 405)

    def test_search_switch_floor_and_display_selected_route_stage(self):
        guest = self.client()
        page = lambda **query: guest.request("/campus-map?"+urlencode(query))[2]
        self.assertIn("Корпус Б · 4-й этаж", page(building="b", floor=4))
        self.assertIn("room=b2-2018a", page(q="ауд. 2018a"))
        self.assertIn("Новый корпус · 4-й этаж", page(room="n4-423"))
        way = {"from":"v2-entry", "to":"n4-423"}
        first = page(**way)
        self.assertIn("Шаг 1 из 4", first)
        self.assertIn("Поднимись по этой лестнице на 3-й этаж.", first)
        bridge = page(**way, step=3)
        self.assertIn("Шаг 3 из 4", bridge)
        self.assertIn("Корпус Г · 3-й этаж", bridge)
        self.assertIn("на 4-й этаж.", bridge)
        final = page(**way, step=4)
        self.assertIn("Шаг 4 из 4", final)
        self.assertIn('class="campus-route-line"', final)
        self.assertIn('aria-current="step"', final)

    def test_route_shows_destination_and_current_plan_before_the_map(self):
        guest = self.client()
        path = "/campus-map?from=v2-entry&to=b1-1012"
        body = guest.request(path)[2]
        summary = body.split('class="campus-route-summary"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Аудитория 1012 · Корпус Б · 1-й этаж", summary)
        self.assertIn("<strong>На схеме</strong> Корпус В · 2-й этаж", summary)
        self.assertIn("Впереди · 1 лестница и 1 переход", summary)
        last = guest.request(path + "&step=3")[2]
        self.assertIn("<strong>На схеме</strong> Корпус Б · 1-й этаж", last)
        self.assertIn("Последний участок по схеме", last)

    def test_lesson_reuses_explicitly_chosen_start_without_affecting_shared_routes(self):
        student = self.client("student-a")
        other = self.client("student-b")
        deep_link = "/campus-map?to=b1-1012&back=/schedule/" + seed_id("a1")
        self.assertIn("Главный вход → 1012", student.request(deep_link)[2])
        status, headers, _ = student.request("/campus-map?from=b2-2018a&to=b1-1012&remember_start=1")
        self.assertEqual(status, 303)
        self.assertNotIn("remember_start", headers["Location"])
        self.assertIn("2018а → 1012", student.request(headers["Location"])[2])
        self.assertIn("2018а → 1012", student.request(deep_link)[2])
        self.assertIn("Сохранённый старт · 2018а", student.request(deep_link)[2])
        self.assertIn("Главный вход → 1012", other.request(deep_link)[2])
        previous = student.cookies.copy()
        student.cookies.update(other.cookies)
        self.assertIn("Главный вход → 1012", student.request(deep_link)[2])
        student.cookies = previous
        self.assertIn("2002 → 1012", student.request(deep_link + "&from=v2-2002")[2])
        student.request("/campus-map?from=v2-entry&to=b1-1012&remember_start=1", HTTP_SEC_FETCH_SITE="cross-site")
        self.assertIn("2018а → 1012", student.request(deep_link)[2])
        student.request("/campus-map?from=v2-entry&to=b1-1012&remember_start=1")
        self.assertIn("Главный вход → 1012", student.request(deep_link)[2])

    def test_unknown_locations_and_same_point_show_recovery_options(self):
        guest = self.client()
        path = "/campus-map?"
        self.assertIn("Выбери точки маршрута из списка.", guest.request(path+"to=unmapped")[2])
        self.assertIn("Ты уже в точке назначения.", guest.request(path+"to=v2-entry")[2])
        for phrase in ("В-205", "205", "00000"):
            with self.subTest(phrase=phrase):
                self.assertIn(f"«{phrase}» не нашлось на этой схеме", guest.request(path+urlencode({"q": phrase}))[2])
        invalid = guest.request(path+urlencode({"q":"<script>alert(1)</script>", "step":"9"*400}))[2]
        self.assertNotIn("<script>alert(1)", invalid)

    def test_cross_floor_route_names_the_staircase_in_a_separate_phrase(self):
        guest = self.client()
        address = "/campus-map?from=v2-2002&to=v3-place-2"
        self.assertIn("Ориентир — «Западная лестница». Поднимись по этой лестнице на 3-й этаж.",
                      guest.request(address)[2])
        self.assertIn("найди «3003» по указателям", guest.request(address + "&step=2")[2])

    def test_unmapped_lesson_explains_the_missing_route_without_linking_to_bookings(self):
        student = self.client("student-b")
        lesson = "/schedule/" + seed_id("b1")
        status, _, body = student.request(lesson)
        self.assertEqual(status, 200)
        self.assertIn("В-205", body)
        self.assertIn("Этой аудитории нет на карте кампуса.", body)
        self.assertIn('href="/support"', body)
        self.assertNotIn("/bookables/", body)
        self.assertNotIn("Построить маршрут", body)
        schedule = student.request(f"/schedule?date={self.today.isoformat()}&view=day")[2]
        self.assertRegex(schedule, r'data-room="" data-location="ДЕМО · В-205"')
        self.assertIn("data-sheet-map-unavailable", schedule)
        self.assertRegex(schedule, r'<a class="rw-primary-link" data-sheet-map hidden>')

    def test_rooms_without_checked_route_show_floor_and_source(self):
        guest = self.client()
        place = campus_map.find(campus_map.campus(), "2047")[0]
        status, _, body = guest.request("/campus-map?room="+place.key)
        self.assertEqual(status, 200)
        self.assertIn('class="campus-room-focus"', body)
        self.assertIn("Маршрут сюда", body)
        self.assertIn("Точное положение двери", body)
        self.assertIn("Официальный план этажа", body)

    def test_repeated_floor_represents_exact_stage(self):
        guest = self.client()
        path = "/campus-map?from=v4-hall-5&to=v4-hall-4&step=5"
        body = guest.request(path)[2]
        self.assertIn("Шаг 5 из 5", body)
        self.assertIn("Завершить маршрут", body)
        self.assertIn("Поменять точки", body)
        self.assertIn('class="campus-route-marker"', body)

    def test_browsing_other_floor_shares_the_saved_step(self):
        guest = self.client()
        path = "/campus-map?from=v2-entry&to=n4-423&building=g&floor=3&progress=2"
        status, _, body = guest.request(path)
        self.assertEqual(status, 200)
        self.assertIn('data-campus-mode="browse"', body)
        self.assertIn("Маршрут приостановлен · шаг 2 из 4", body)
        self.assertIn("К шагу 3", body)
        self.assertNotIn('class="campus-route-line"', body)
        share = re.search(r'data-campus-share="([^"]+)"', body)
        self.assertIsNotNone(share)
        address = unescape(share[1])
        self.assertEqual(parse_qs(urlsplit(address).query)["step"], ["2"])
        reopened = guest.request(address)[2]
        self.assertIn("Шаг 2 из 4", reopened)
        self.assertIn("Корпус В · 3-й этаж", reopened)
        self.assertIn('class="campus-route-line"', reopened)

    def test_suggestions_keep_location_only_and_route_stage(self):
        guest = self.client()
        status, _, response = guest.request("/campus-map/suggest?q=2047&from=v2-entry&to=n4-423&progress=2")
        self.assertEqual(status, 200)
        location = json.loads(response)["results"][0]
        self.assertTrue(location["location_only"])
        self.assertEqual(parse_qs(urlsplit(location["route"]).query), {"from":["v2-entry"], "to":["b2-place-36"]})
        self.assertEqual(parse_qs(urlsplit(location["href"]).query)["progress"], ["2"])

    def test_canteen_search_opens_the_campus_landmark(self):
        guest = self.client()
        for query in ("столовая", "трёшка", "Треха", "корпус Ж"):
            with self.subTest(query=query):
                params = urlencode({"q": query, "from": "n4-436", "to": "n4-409", "progress": 1})
                _, _, response = guest.request("/campus-map/suggest?" + params)
                result = json.loads(response)["results"][0]
                self.assertEqual(result["place"], "canteen")
                self.assertFalse(result["route"])
                self.assertEqual(parse_qs(urlsplit(result["href"]).query)["progress"], ["1"])
                body = guest.request(result["href"])[2]
                self.assertIn('class="campus-grounds-food-foot"', body)
                self.assertIn('aria-current="location"', body)
                self.assertIn("Корпус Ж", body)
                self.assertIn("Начать маршрут", body)
                self.assertIn("Продолжить маршрут", body)
        body = guest.request("/campus-map?" + urlencode({"q": "столовая"}))[2]
        self.assertIn("view=grounds&amp;place=canteen", body)

    def test_canteen_navigation_hands_off_at_the_mapped_staircase(self):
        guest = self.client()
        data = campus_map.campus()
        end = campus_map.CANTEEN_APPROACH
        legs = campus_map.stages(campus_map.route(data, campus_map.ENTRY, end))
        self.assertEqual([leg[0].floor for leg in legs], ["v2", "v3", "g3"])
        self.assertEqual(legs[-1][-1].key, end)
        path = f"/campus-map?from={campus_map.ENTRY}&to={end}&step=3"
        body = guest.request(path)[2]
        self.assertIn('class="campus-route-line"', body)
        self.assertIn("до лестницы у 3154.", body)
        self.assertIn("Дальше к столовой", body)
        self.assertNotIn("Завершить маршрут", body)
        handoff = guest.request("/campus-map?view=grounds&place=canteen&via=stairs")[2]
        self.assertIn("Спустись по лестнице у 3154", handoff)
        self.assertIn("Студенческий зал на 2-м этаже", handoff)
        self.assertNotIn('class="campus-route-line"', handoff)

    def test_default_start_keeps_the_destination_field_accessible(self):
        guest = self.client()
        overview = guest.request("/campus-map")[2]
        self.assertIn('<label for="campus-query">Куда идти?</label>', overview)
        self.assertIn('<strong>Откуда</strong><span>Главный вход</span>', overview)
        body = guest.request("/campus-map?room=v2-entry")[2]
        self.assertIn('<section id="campus-search"', body)
        self.assertIn('href="#campus-query"', body)
        self.assertIn('Выбрать, куда идти', body)

    def test_opening_a_room_pauses_the_saved_route_even_on_its_floor(self):
        guest = self.client()
        way = "from=n4-436&to=n4-409&progress=1"
        for key, title in (("b1-place-32", "1045"), ("n4-423", "Зал 423")):
            with self.subTest(room=title):
                body = guest.request(f"/campus-map?{way}&room={key}")[2]
                self.assertIn(f"{title}</h2>", body)
                self.assertIn("Маршрут приостановлен · шаг 1 из 1", body)
                self.assertIn("Продолжить маршрут", body)
                self.assertNotIn('class="campus-route-line"', body)
                self.assertNotIn("Завершить маршрут", body)
        reopened = guest.request(f"/campus-map?{way}&room=n4-423&step=1")[2]
        self.assertIn('class="campus-route-line"', reopened)
        self.assertNotIn('id="campus-place"', reopened)
        self.assertIn('<p class="campus-level-static">4-й этаж</p>', reopened)
        self.assertIn('class="campus-button campus-map-search" href="#campus-search"', reopened)
