from dataclasses import dataclass
from ipaddress import ip_address
from os import getenv
import re
from urllib.parse import urlsplit

from .domain import DomainError

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
LOCAL_AUTHORITY = re.compile(r"(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?", re.IGNORECASE)
HOSTNAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?")


@dataclass(frozen=True)
class WebConfig:
    public_origin: str | None = None
    trust_proxy: bool = False

    def __post_init__(self):
        if not self.public_origin:
            if self.trust_proxy:
                raise ValueError("DEAN_TRUST_PROXY требует заданного DEAN_PUBLIC_ORIGIN.")
            return
        try:
            origin = urlsplit(self.public_origin)
            host = origin.hostname
            port = origin.port
        except ValueError:
            raise ValueError("DEAN_PUBLIC_ORIGIN должен быть HTTPS-адресом без пути.") from None
        if host and HOSTNAME.fullmatch(host) and port != 0:
            authority = f"{host}:{port}" if port not in (None, 443) else host
            if self.public_origin == f"https://{authority}":
                return
        raise ValueError("DEAN_PUBLIC_ORIGIN должен быть HTTPS-адресом без пути.")

    @classmethod
    def from_env(cls):
        origin = getenv("DEAN_PUBLIC_ORIGIN") or None
        secure = getenv("DEAN_SECURE_COOKIES")
        trust = getenv("DEAN_TRUST_PROXY", "0")
        if secure not in (None, "0", "1") or trust not in {"0", "1"}:
            raise ValueError("Булевы параметры DEAN принимают 0 или 1.")
        if secure == "1" and not origin:
            raise ValueError("Для DEAN_SECURE_COOKIES=1 укажи DEAN_PUBLIC_ORIGIN.")
        if secure == "0" and origin:
            raise ValueError("Публичный режим требует Secure cookies.")
        return cls(origin, trust == "1")

    def request_origin(self, env):
        host = env.get("HTTP_HOST")
        scheme = env["wsgi.url_scheme"]
        if self.public_origin:
            if host and f"{scheme}://{host.lower()}" == self.public_origin:
                return self.public_origin
            raise DomainError(400, "Адрес или протокол запроса не соответствует настройкам приложения.")
        if host is None and env.get("SERVER_NAME") in LOOPBACK:
            host = env["SERVER_NAME"]
            if env["SERVER_PORT"] != "80":
                host += ":" + env["SERVER_PORT"]
        match = LOCAL_AUTHORITY.fullmatch(host or "")
        if scheme != "http" or not match or (match[2] and not 1 <= int(match[2]) <= 65535):
            raise DomainError(400, "Для локальной версии используй адрес loopback.")
        canonical_host = match[1].lower()
        if match[2] and int(match[2]) != 80:
            canonical_host += f":{int(match[2])}"
        return f"http://{canonical_host}"

    def request_address(self, env):
        address = env.get("REMOTE_ADDR", "")
        if self.trust_proxy:
            try:
                return str(ip_address(env.get("HTTP_X_REAL_IP", "")))
            except ValueError:
                pass
        return address
