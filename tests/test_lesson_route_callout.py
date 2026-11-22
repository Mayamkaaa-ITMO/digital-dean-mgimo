from urllib.parse import urlencode

from dean import activity_extensions
from dean.seed import seed_id
from tests.test_dean import DemoFixture


class LessonRouteCalloutTest(DemoFixture):
    def test_route_callout_uses_selected_room_and_returns_to_the_lesson(self):
        student = self.client("student-a")
        staff = self.client("staff")
        event_id = seed_id("a1")
        classic = "/schedule/" + event_id
        raspay = "/schedule/raspay/lesson/" + event_id
        target = "b1-1012"
        self.assertNotIn("Построить маршрут", student.request(classic)[2])
        staff_path = "/staff" + classic
        room = activity_extensions.rooms()[target]
        self.assertIn(f'<option value="{room}">', staff.request(staff_path)[2])
        edit = staff.fields(staff_path, staff_path) | {"room": room}
        self.assertEqual(staff.request(staff_path, edit)[0], 303)
        for path in (classic, raspay):
            status, _, content = student.request(path)
            self.assertEqual(status, 200)
            self.assertIn("Как пройти на занятие", content)
            self.assertIn("Построить маршрут", content)
            query = urlencode({"to": target, "back": path})
            href = "/campus-map?" + query
            self.assertIn(href.replace("&", "&amp;"), content)
            status, _, mapped = student.request(href)
            self.assertEqual(status, 200)
            self.assertIn(f'href="{path}" class="campus-button">К занятию</a>', mapped)
            self.assertIn("Главный вход → 1012", mapped)
        student.request("/campus-map?from=b2-2018a&remember_start=1")
        for path in (classic, raspay):
            href = "/campus-map?" + urlencode({"to": target, "back": path})
            self.assertIn(href.replace("&", "&amp;"), student.request(path)[2])
            self.assertIn("2018а → 1012", student.request(href)[2])
