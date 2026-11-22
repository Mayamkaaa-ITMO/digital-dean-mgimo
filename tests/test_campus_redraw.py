"""Room outlines, wall apertures and route origin share the same public view."""
import json
from html.parser import HTMLParser
from unittest import TestCase
from urllib.parse import parse_qs, urlencode, urlsplit

from dean import campus_map
from dean.campus_geometry import display_plans, floor_paths
from dean.campus_map_view import display_labels, plan
from tests.test_dean import DemoFixture


class FloorMarkup(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.shapes = {}
        self.rooms = {}
        self.current = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        classes = attr.get("class", "").split()
        if "campus-area" in classes:
            self.shapes[int(attr["data-campus-index"])] = attr["d"]
        elif "campus-walls" in classes:
            self.walls = attr["d"]
        elif tag == "a" and "campus-room" in classes:
            self.current = parse_qs(urlsplit(attr["href"]).query)["room"][0]
            self.rooms[self.current] = {}
        elif self.current and attr.get("class") in ("campus-room-hit", "campus-room-focus"):
            self.rooms[self.current][attr["class"]] = attr.get("d")

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None


class MapLinks(HTMLParser):
    def __init__(self, page):
        super().__init__()
        self.links = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and "href" in attrs:
            query = parse_qs(urlsplit(attrs["href"]).query)
            self.links.append((attrs, {key: values[0] for key, values in query.items()}))

    def find(self, *, attrs=None, class_name="", **query):
        return next(link["href"] for link, values in self.links
                    if all(values.get(key) == val for key, val in query.items())
                    and all(link.get(key) == value for key, value in (attrs or {}).items())
                    and (not class_name or class_name in link.get("class", "").split()))


class CampusRedrawTests(TestCase):
    def test_each_floor_uses_one_shape_for_architecture_and_hit_target(self):
        data = campus_map.campus()
        for key, floor in data.floors.items():
            with self.subTest(floor=key):
                layout = display_labels(data, floor)
                assigned = [i for _, _, _, i in layout]
                page = plan(data, key, None, (), {})
                doc = FloorMarkup(page)
                self.assertEqual(set(doc.rooms), {place for place, *_ in layout})
                for place, _, _, i in layout:
                    if i is not None and assigned.count(i) == 1:
                        self.assertEqual(doc.rooms[place]["campus-room-hit"], doc.shapes[i])
                self.assertNotIn("campus-corridors", page)
                self.assertEqual(doc.walls, display_plans()[key]["walls"])
                self.assertGreater(doc.walls.count("M"), 2)
                self.assertEqual(doc.walls.count("M"), doc.walls.count("Z"))
                self.assertIn(f'viewBox="-10 -10 {floor.width+20} {floor.height+20}"', page)
                for shape in doc.shapes.values():
                    self.assertTrue(shape.upper().endswith("Z"))
                    self.assertNotRegex(shape.lower(), r"nan|inf")

    def test_v2_rectangles_and_lobby_symbols(self):
        data = campus_map.campus()
        doc = FloorMarkup(plan(data, "v2", "v2-2004", (), {}))
        for i, key in ((20, "v2-2002"), (36, "v2-2004")):
            self.assertRegex(doc.shapes[i], r"^M[\d.]+ [\d.]+(?:[HV][\d.]+){3}Z$")
            self.assertIn(doc.shapes[i], doc.walls)
            self.assertEqual(doc.rooms[key]["campus-room-hit"], doc.shapes[i])
        self.assertEqual(doc.shapes[36], doc.rooms["v2-2004"]["campus-room-focus"])
        self.assertFalse({33, 45, 50, 51, 52, 53, 54, 56} & doc.shapes.keys())
        self.assertNotIn("<title>Аудитория 2002", plan(data, "v2", None, (), {}))

    def test_preserve_authored_curves_ambiguous_spaces_and_real_notches(self):
        walls = "M0 0H80V80H0ZM8 9V23H14V18H31V9Z"
        notch = "M9 9V23H14V18H30V9Z"
        self.assertEqual(floor_paths(walls, (notch,), frozenset({0}))[1][0], "M8 9V23H14V18H31V9Z")
        for key in ("a1", "a2"):
            f = campus_map.campus().floors[key]
            paths = tuple(p for _, p, _ in f.areas)
            self.assertEqual(floor_paths(f.walls, paths, frozenset()), (f.walls, paths))


class OriginLookupTests(DemoFixture):
    def test_floor_building_and_svg_origin_retain_destination_and_return(self):
        client = self.client("student-a")
        back = "/schedule/" + "a" * 32
        def read(path):
            code, _, page = client.request(path)
            self.assertEqual(code, 200)
            return page

        room = read("/campus-map?" + urlencode({"room": "a1-cafe", "back": back}))
        mode = MapLinks(room).find(field="from", target="a1-cafe", back=back)
        origin_editor = read(mode)
        self.assertIn("Откуда идёшь?", origin_editor)
        upstairs = MapLinks(origin_editor).find(field="from", target="a1-cafe", back=back,
                                               building="a", floor="2", attrs={"aria-label": "2-й этаж"})
        upper_editor = read(upstairs)
        self.assertIn("Откуда идёшь?", upper_editor)
        self.assertIn("Кафе Монте-Кристо", upper_editor)
        hall = MapLinks(upper_editor).find(class_name="campus-room", **{"from": "a2-conference",
                                                                      "target": "a1-cafe", "back": back})
        self.assertNotIn("room=", hall)
        status, headers, _ = client.request(hall)
        self.assertEqual(status, 303)
        self.assertNotIn("remember_start", headers["Location"])
        picked = read(headers["Location"])
        self.assertIn("campus-point-start", picked)
        self.assertIn("Куда идти?", picked)
        self.assertIn("Кафе Монте-Кристо", picked)
        route = MapLinks(picked).find(class_name="campus-search-route",
                                     **{"from": "a2-conference", "to": "a1-cafe", "back": back})
        self.assertIn('data-campus-mode="route"', read(route))
        switch_building = MapLinks(picked).find(building="b", target="a1-cafe",
                                              **{"from": "a2-conference", "back": back})
        self.assertIn("Кафе Монте-Кристо", read(switch_building))
        grounds = MapLinks(picked).find(view="grounds", target="a1-cafe", back=back)
        self.assertIn(f'href="{back}"', read(grounds))

    def test_edit_destination_keeps_origin_and_replaces_only_destination(self):
        client = self.client()
        route = client.request("/campus-map?from=v2-entry&to=n4-423&step=2")[2]
        edit = MapLinks(route).find(field="to", to="n4-423", step="2")
        editor = client.request(edit)[2]
        browse = MapLinks(editor).find(building="v", floor="4", field="to", to="n4-423", progress="2")
        paused = client.request(browse)[2]
        self.assertIn("Маршрут приостановлен · шаг 2 из 4", paused)
        picked = MapLinks(paused).find(class_name="campus-room", step="1", to="v4-hall-4",
                                      **{"from": "v2-entry"})
        self.assertNotIn("progress=", picked)
        self.assertNotIn("to=n4-423", picked)
        new = client.request(picked)[2]
        self.assertIn("Главный вход", new)
        self.assertIn("Шаг 1 из", new)

    def test_current_origin_and_destination_survive_tab_navigation_and_submit(self):
        client = self.client()
        for state, expected in (({}, {"from": ["b1-1012"]}),
                                ({"room": "n4-423"}, {"target": ["n4-423"], "from": ["b1-1012"]}),
                                ({"to": "n4-423", "step": "3"}, {"from": ["b1-1012"], "to": ["n4-423"], "step": ["1"]}),
                                ({"view": "grounds", "place": "canteen"},
                                 {"from": ["b1-1012"], "view": ["grounds"], "place": ["canteen"]})):
            query = {"field": "from", "q": "1012", "from": "n4-436", **state}
            api = "/campus-map/suggest?" + urlencode(query)
            row = json.loads(client.request(api)[2])["results"][0]
            status, headers, _ = client.request("/campus-map?" + urlencode({**query, "pick": 1}))
            self.assertEqual(status, 303)
            self.assertEqual(headers["Location"], row["href"])
            params = parse_qs(urlsplit(row["href"]).query)
            self.assertEqual({k: params[k] for k in expected}, expected)
            self.assertNotIn("progress", params)
            self.assertEqual(client.request(row["href"].split("#")[0])[0], 200)
        for q, expected in (("2018 a", "b2-2018a"), ("2018", "b2-2018"), ("кафе", "a1-cafe")):
            rows = json.loads(client.request("/campus-map/suggest?" + urlencode({"q":q, "field":"from"}))[2])["results"]
            self.assertIn("from=" + expected, rows[0]["href"])

    def test_plan_room_can_become_origin_in_suggestions_and_fast_submit(self):
        client = self.client()
        params = {"from":"n4-436", "to":"n4-409", "field":"from", "q":"2047", "pick":1}
        status, headers, _ = client.request("/campus-map?" + urlencode(params))
        self.assertEqual(status, 303)
        row = json.loads(client.request("/campus-map/suggest?" + urlencode(params))[2])["results"][0]
        self.assertEqual(row["href"], headers["Location"])
        self.assertEqual(parse_qs(urlsplit(row["href"]).query),
                         {"from":["b2-place-36"], "to":["n4-409"], "step":["1"], "remember_start":["1"]})
        self.assertIn("2047 → 409", client.request(row["href"].split("#")[0])[2])
