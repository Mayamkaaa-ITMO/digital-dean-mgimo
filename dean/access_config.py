from dataclasses import dataclass
from importlib.util import find_spec
from os import getenv
import re

LABELS = {"max": "MAX", "vk": "VK ID", "yandex": "Яндекс ID", "key": "Ключ доступа"}
SOCIAL = ("vk", "yandex")


@dataclass(frozen=True)
class AccessConfig:
    max_name: str = ""
    max_token: str = ""
    vk_client: str = ""
    yandex_client: str = ""
    yandex_secret: str = ""
    passkeys: bool = False

    @classmethod
    def from_env(cls):
        return cls(getenv("DEAN_MAX_BOT_USERNAME", ""), getenv("DEAN_MAX_BOT_TOKEN", ""),
                   getenv("DEAN_VK_CLIENT_ID", ""),
                   getenv("DEAN_YANDEX_CLIENT_ID", ""), getenv("DEAN_YANDEX_CLIENT_SECRET", ""),
                   getenv("DEAN_PASSKEYS") == "1")

    def enabled(self, provider, origin):
        if provider == "key":
            return (self.passkeys and bool(find_spec("webauthn"))
                    and (origin.startswith("https://") or bool(re.fullmatch(r"http://localhost(?::\d+)?", origin))))
        if not origin.startswith("https://"):
            return False
        if provider == "max":
            return bool(self.max_token.strip() and re.fullmatch(r"[a-zA-Z0-9_]{3,64}", self.max_name))
        if provider == "vk":
            return bool(re.fullmatch(r"[0-9]{1,24}", self.vk_client))
        if provider == "yandex":
            return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.yandex_client) and self.yandex_secret.strip())
        return False
