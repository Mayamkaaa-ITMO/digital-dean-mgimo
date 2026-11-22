"""Public planner coverage, including rooms located only by their floor plan."""
from itertools import pairwise, product
import re
from unittest import TestCase

from dean import campus_map as maps
from dean.campus_geometry import display_plans
from dean.campus_map_view import plan, point_link
from tests.test_dean import DemoFixture


class NavigationTests(TestCase):
    def test_every_ordered_pair_inside_each_building(self):
        data = maps.campus()
        nav = data.navigation
        for building in data.buildings:
            places = [p for p in data.choices.values() if p.floor.startswith(building)]
            for a, b in product(places, repeat=2):
                path = nav.route(a.key,b.key)
                self.assertEqual((path[0],path[-1]), (a,b))
                self.assertEqual(len(set(path)),len(path))
                for u,v in pairwise(path):
                    if u.key in nav.approaches:
                        self.assertEqual(nav.approaches[u.key],v.key)
                        self.assertEqual(u.floor,v.floor)
                    elif v.key in nav.approaches:
                        self.assertEqual(nav.approaches[v.key],u.key)
                        self.assertEqual(u.floor,v.floor)
                    else:
                        self.assertIn(v.key,nav.edges[u.key])
                    if u.floor != v.floor:
                        self.assertEqual(u.kind,v.kind)
                        self.assertIn(u.kind,("stairs","passage"))
                if any(p.floor[0] != building for p in path):
                    self.assertIn("v4-hall-4",(a.key,b.key))

    def test_foyer_route_does_not_draw_a_segment_through_the_wall(self):
        data = maps.campus()
        for a,b in (("a1-cafe","a2-place-11"),("a2-place-11","a1-cafe")):
            steps = maps.stages(data.navigation.route(a,b))
            self.assertEqual({s[0].floor for s in steps},{"a1","a2"})
            step, = [s for s in steps if s[0].floor=="a2"]
            svg = plan(data,"a2","",step,{"from":a,"to":b})
            points = re.search(r'<polyline class="campus-route-line" points="([^"]+)"',svg)[1].split()
            self.assertIn("118,393",points)
            self.assertNotIn("43,390",points)
        shapes = display_plans()["v2"]
        self.assertRegex(shapes["areas"][31],r"^M[\d.]+ [\d.]+(?:L[\d.]+ [\d.]+){3}Z$")
        self.assertIn(shapes["areas"][31],shapes["walls"])
        a2 = display_plans()["a2"]
        self.assertIn(a2["areas"][12],a2["walls"])
        self.assertIn(a2["areas"][13],a2["walls"])

    def test_intermediate_b_floor_and_isolated_east_landing(self):
        data = maps.campus()
        for a,b in (("b1-1012","b4-4017"),("b4-4017","b1-1012")):
            steps=maps.stages(data.navigation.route(a,b))
            self.assertEqual({s[0].floor for s in steps},{"b1","b2","b4"})
        path=data.navigation.route("v2-entry","v4-place-25")
        self.assertEqual(path[-2].kind,"stairs")
        self.assertEqual(path[-2].floor,"v4")


class NavigationLinksTests(DemoFixture):
    def test_change_destination_keeps_start_and_edit_link_opens_server_form(self):
        data = maps.campus()
        new = data.places["b1-1012"]
        url = point_link(data, new, {"from": "v2-entry", "to": "b4-4017", "field": "to"})
        self.assertIn("from=v2-entry&to=b1-1012", url)
        web = self.client()
        self.assertIn('data-campus-mode="route"', web.request(url)[2])
        html = web.request(url.split("#")[0] + "&edit=1")[2]
        self.assertIn('class="campus-disclosure campus-edit-route" open', html)

    def test_identical_place_overview_resumes_and_invalid_id_recovers(self):
        web = self.client()
        q = "/campus-map?from=a2-place-11&to=a2-place-11"
        self.assertIn("Ты уже в точке назначения",web.request(q)[2])
        self.assertIn("Продолжить маршрут",web.request(q+"&building=a&floor=2&progress=1")[2])
        self.assertIn("Ты уже в точке назначения",web.request(q+"&step=1")[2])
        self.assertIn("Маршрут не построен",web.request("/campus-map?to=absent")[2])
