"""End-to-end contracts for the verified campus data and its public interfaces."""
import math
import re
from collections import deque
from itertools import combinations, pairwise
from unittest import TestCase
from urllib.parse import parse_qs, urlencode, urlsplit

from dean import campus_demo, campus_map as maps
from dean.campus_map_view import display_labels, drawing_path, search_matches
from tests.test_dean import DemoFixture


class CampusDataTests(TestCase):
    def test_search_round_trip_for_entire_directory_and_lettered_rooms(self):
        data = maps.campus()
        for point in data.directory():
            with self.subTest(key=point.key):
                hit = search_matches(data, point.label, {}, data.floors[point.floor])[0]
                self.assertEqual(parse_qs(urlsplit(hit["href"]).query)["room"], [point.key])
                self.assertEqual(urlsplit(hit["href"]).fragment, "campus-place")
                self.assertEqual(hit["location_only"], point.kind == "location")
                match = re.match(r"^(\d+)([а-я])(?: |$)", point.label)
                if match:
                    n, letter = match.groups()
                    latin = {"а": "a", "б": "b", "в": "v", "к": "k", "г": "g"}.get(letter, letter)
                    for suffix in (letter, letter.upper(), latin.upper()):
                        for gap in ("", "-", " ", "‑"):
                            self.assertIn(point, maps.find(data, n + gap + suffix))

    def test_numbers_word_errors_and_context(self):
        data = maps.campus()
        for query in ("2018а", "2018-а", "2018-А", "2018 a", "2018a", "кабинет 2018-А корпус Б этаж 2"):
            self.assertEqual([p.key for p in maps.find(data, query)], ["b2-2018a"])
        for q in ("", "000000", "2018а корпус В", "2018а этаж 3", "2018я", "зюзялам"):
            self.assertEqual(maps.find(data, q), [])
        self.assertEqual(maps.find(data, "2018")[0].key, "b2-2018")
        self.assertEqual({p.label for p in maps.find(data, "2003b")}, {"2003б", "2003в"})
        self.assertEqual([p.label for p in maps.find(data, "2005b")], ["2005б"])
        self.assertIsNone(campus_demo.exact_room(data, "v", 2, "2003b"))
        self.assertEqual(maps.find(data, "главнй вход")[0].key, maps.ENTRY)
        self.assertEqual(maps.find(data, "кафе монте кристо")[0].key, "a1-cafe")
        self.assertEqual({p.floor for p in maps.find(data, "Корпус В 4-й этаж")}, {"v4"})

    def test_each_route_follows_recorded_edges_and_has_minimum_cost(self):
        data = maps.campus()
        ends = list(data.points())
        for start in ends:
            # Independent queue relaxation oracle, without sharing the router's heap or cache.
            queue = deque([start.key])
            distances = {start.key: 0}
            while queue:
                key = queue.popleft()
                for other, weight in data.neighbors[key]:
                    candidate = distances[key] + weight
                    if candidate < distances.get(other, math.inf):
                        distances[other] = candidate
                        queue.append(other)
            for end in ends:
                path = maps.route(data, start.key, end.key)
                self.assertEqual((path[0], path[-1]), (start, end))
                self.assertEqual(len(path), len(set(path)))
                cost = sum(dict(data.neighbors[a.key])[b.key] for a, b in pairwise(path))
                self.assertAlmostEqual(cost, distances[end.key])
        self.assertEqual(maps.route(data, maps.ENTRY, "b2-place-36"), [])
        self.assertEqual(maps.route(data, "missing", "missing"), [])

    def test_same_floor_detours_only_between_disconnected_sections(self):
        data = maps.campus()
        detours = []
        for a, b in combinations(data.points(), 2):
            if a.floor != b.floor:
                continue
            if len(maps.stages(maps.route(data, a.key, b.key))) == 1:
                continue
            seen, queue = {a.key}, [a.key]
            while queue:
                for other, _ in data.neighbors[queue.pop()]:
                    if data.places[other].floor == a.floor and other not in seen:
                        seen.add(other)
                        queue.append(other)
            self.assertNotIn(b.key, seen, (a.key, b.key))
            detours.append(a.floor)
        self.assertEqual(detours, ["v4"] * 11)

    def test_all_plans_and_drawn_rooms_remain_inside_floor(self):
        data = maps.campus()
        for key, floor in data.floors.items():
            for shape in [floor.walls, *(a[1] for a in floor.areas)]:
                display = drawing_path(shape)
                self.assertEqual(display.count("M"), shape.count("M"), key)
                self.assertEqual(display.upper().count("Z"), shape.upper().count("Z"), key)
                self.assertNotRegex(display.lower(), "nan|infinity")
            for _, _, (x, y, w, h) in floor.areas:
                self.assertTrue(0 <= x < x + w <= floor.width and 0 <= y < y + h <= floor.height, key)
            for place, x, y, area in display_labels(data, floor):
                self.assertEqual(data.places[place].floor, key)
                self.assertTrue(0 <= x <= floor.width and 0 <= y <= floor.height, key)
                self.assertTrue(area is None or 0 <= area < len(floor.areas), key)
            for a, b in floor.corridors:
                self.assertIn(b, dict(data.neighbors[a]))
                self.assertEqual((data.places[a].floor, data.places[b].floor), (key, key))

    def test_schedule_links_are_exact_and_scoped(self):
        data = maps.campus()
        for row in (*campus_demo.LESSONS, *campus_demo.STAFF):
            self.assertIsNotNone(campus_demo.exact_room(data, *row[-3:]))
        for fields in (("v", 2, "2018-а"), ("b", 3, "2018а"), ("Корпус МО", 2, "2018а"), ("b", 2, "2018я")):
            self.assertIsNone(campus_demo.exact_room(data, *fields))
        self.assertEqual(campus_demo.exact_room(data, "b", 2, "2018").key, "b2-2018")


class FinalCampusWebTests(DemoFixture):
    def test_fast_submit_without_javascript_and_plan_only_recovery(self):
        client = self.client()
        for q, key in (("2018-А", "b2-2018a"), ("2047", "b2-place-36")):
            status, headers, _ = client.request("/campus-map?" + urlencode({"q": q, "pick": 1}))
            self.assertEqual(status, 303)
            self.assertIn(f"room={key}#campus-place", headers["Location"])
        status, _, body = client.request("/campus-map?to=b2-place-36")
        self.assertEqual(status, 200)
        self.assertIn("Главный вход → 2047", body)
        self.assertIn('class="campus-route-line"', body)
        _, _, last = client.request("/campus-map?to=b2-place-36&step=2")
        self.assertIn("Точное положение двери", last)
        self.assertIn("Корпус Б · 2-й этаж", last)

    def test_demo_entries_canteen_and_shared_step(self):
        client = self.client()
        for path, expected in (("/universities?q=кампус", campus_demo.GROUP),
                               (campus_demo.SCHEDULE, "Группа и занятия вымышлены"),
                               (campus_demo.DIRECTORY, "вымышлены")):
            code, _, body = client.request(path.split("?")[0] + "?" + urlencode(
                {k:v[0] for k,v in parse_qs(urlsplit(path).query).items()}))
            self.assertEqual(code, 200)
            self.assertIn(expected, body)
        _, _, schedule = client.request(campus_demo.SCHEDULE)
        self.assertRegex(schedule, r'<br><a href="[^"]*room=b2-2018a#campus-place" class="rw-primary-link">')
        _, _, body = client.request("/campus-map?view=grounds&place=canteen&from=n4-436")
        self.assertIn("from=n4-436&amp;to=g3-canteen-stairs", body)
        _, _, body = client.request("/campus-map?from=v2-entry&to=g3-canteen-stairs&step=3")
        self.assertRegex(body, r'via=stairs[^"]*progress=3')
        _, _, body = client.request("/campus-map?view=grounds&place=canteen&via=stairs&from=v2-entry&to=g3-canteen-stairs&progress=3")
        self.assertIn("Продолжить маршрут", body)
        self.assertIn("Спустись по лестнице у 3154", body)
