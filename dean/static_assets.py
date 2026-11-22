"""Public assets and their content-addressed URLs for one application release."""

from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from pathlib import Path


STATIC = Path(__file__).with_name("static")
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}
FONTS = ("nunito-cyrillic.woff2", "nunito-latin.woff2", "manrope.ttf")
FILES = (
    *FONTS, "campus-lab.js", "door-counter.js", "field-camera.js", "campus-lab.css", "railway-campus.jpg", "main.js", "auth.js", "railway.js", "raspay.js",
    "main.css", "access.css", "railway.css", "raspay.css", "navigation.css", "campus-map.css", "campus-map.js",
    "assisted.css", "experience.css", "experience.js",
    "design-final.css", "chat.js", "assistant-voice.js", "assistant-mascot.svg", "login-mascot.js", "lecture-library.js", "lecture-library.css",
    "schedule-layout.css", "schedule-layout.js", "edition.css", "edition.js", "edition-login.css", "bookings.css", "bookings.js",
    "notifications.css", "telegram-avatar.jpg",
    "campus-atrium-login.jpg", "campus-atrium-login-mobile.jpg", "booking-room.jpg", "yandex-id.svg", "vk-id.svg",
)


@dataclass(frozen=True)
class Asset:
    content: bytes
    content_type: str
    version: str


@cache
def assets():
    result = {}
    for name in FILES:
        content = (STATIC / name).read_bytes()
        suffix = Path(name).suffix
        if suffix == ".css":
            for font in FONTS:
                route = f"/static/{font}"
                content = content.replace(f'"{route}"'.encode(), f'"{route}?v={result[font].version}"'.encode())
        result[name] = Asset(content, CONTENT_TYPES[suffix], sha256(content).hexdigest()[:24])
    return result


def asset_url(name):
    return f"/static/{name}?v={assets()[name].version}"


def asset_response(path, query, validators):
    asset = assets().get(path.removeprefix("/static/"))
    if asset is None or (query and query != f"v={asset.version}"):
        return 404, b"", []
    etag = f'"{asset.version}"'
    policy = "public, max-age=31536000, immutable" if query else "public, max-age=0, must-revalidate"
    headers = [("Cache-Control", policy), ("ETag", etag)]
    if any(value.strip().removeprefix("W/") in {"*", etag} for value in validators.split(",")):
        return 304, b"", headers
    return 200, asset.content, headers + [("Content-Type", asset.content_type)]
