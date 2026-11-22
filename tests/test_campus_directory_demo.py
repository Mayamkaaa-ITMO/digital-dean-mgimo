from unittest.mock import patch
from urllib.parse import urlencode

from dean import campus_directory

from .test_dean import DemoFixture


class DirectoryDemoTest(DemoFixture):
    def test_opt_in_filters_and_existing_map_binding(self):
        a = self.client("student-a")
        path = campus_directory.PATH
        self.assertEqual(a.request(path)[0], 404)
        with patch.dict("os.environ", {"DEAN_EXT_CAMPUS_DIRECTORY": "1"}):
            response = a.request(path + "?" + urlencode({"kind": "teacher", "q": "синтетова"}))
            self.assertEqual(response[0], 200)
            self.assertIn("Лада Синтетова", response[2])
            self.assertIn("/campus-map?to=b1-1012", response[2])
            self.assertNotIn("Учебная лаборатория макетов", response[2])
            self.assertEqual(a.request("/campus-map?to=b1-1012")[0], 200)

    def test_staff_returns_from_demo_directory_to_staff_home(self):
        employee = self.client("staff")
        with patch.dict("os.environ", {"DEAN_EXT_CAMPUS_DIRECTORY": "1"}):
            status, _, body = employee.request(campus_directory.PATH)
            self.assertEqual(status, 200)
            self.assertIn("В кабинет сотрудника", body)
            self.assertNotIn('href="/all-services"', body)
            self.assertEqual(employee.request("/staff")[0], 200)
