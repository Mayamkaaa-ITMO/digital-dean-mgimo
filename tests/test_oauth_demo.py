from urllib.parse import parse_qs, urlsplit

from scripts.oauth_demo import DemoGateway, save
from .test_dean import Client, DemoFixture, Forms


class OAuthDemoTest(DemoFixture):
    def test_tunnel_uses_current_hostname_and_reloads_both_providers(self):
        host = "candidate-dean.trycloudflare.com"
        origin = "https://" + host
        settings = self.path.parent / "tunnel-providers.json"
        gateway = DemoGateway(self.path, origin, settings)
        client = Client(gateway)
        forwarded = {"HTTP_HOST": host, "HTTP_X_FORWARDED_PROTO": "https"}
        status, headers, _ = client.request("/healthz", **forwarded)
        self.assertEqual((status, headers["X-Dean-Instance"]), (200, gateway.marker))
        self.assertEqual(client.request("/healthz", HTTP_HOST=host)[0], 400)
        self.assertEqual(client.request("/healthz", **dict(forwarded, HTTP_HOST="other.trycloudflare.com"))[0], 400)
        self.assertEqual(client.request("/healthz", **dict(forwarded, REMOTE_ADDR="192.0.2.11"))[0], 400)
        keys = {"vk_client": "111111", "yandex_client": "test-client", "yandex_secret": "test-secret"}
        save(settings, {"origin": "https://previous.trycloudflare.com", "keys": keys})
        self.assertNotIn("/auth/vk/start", Forms(client.request("/login", **forwarded)[2]).forms)
        save(settings, {"origin": origin, "keys": keys})
        for provider in ("yandex", "vk"):
            action = f"/auth/{provider}/start"
            fields = Forms(client.request("/login", **forwarded)[2]).forms[action]
            status, headers, _ = client.request(action, fields, HTTP_ORIGIN=origin, **forwarded)
            self.assertEqual(status, 303)
            returned = parse_qs(urlsplit(headers["Location"]).query)
            self.assertEqual(returned["redirect_uri"], [origin + f"/auth/{provider}/callback"])
        self.assertEqual(settings.stat().st_mode & 0o777, 0o600)
