"""Budgeted, fixed-destination calls for the dean's two AI entry points."""
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
from http.client import HTTPException, HTTPSConnection
import json
from os import getenv
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

from .clock import now
from .db import write
from .domain import DomainError, timestamp

CHAT_MODEL = "deepseek/deepseek-v4.1-flash-1017"
PHOTO_MODEL = "qwen/qwen3.6-35b-a3b"
HOST = "routerai.ru"
TIMEOUT = 23
PENDING_RETRY_WINDOW = timedelta(minutes=2)
MAX_RESPONSE = 512 * 1024
ACCOUNT_LIMITS = {"photo": 5, "assistant": 50, "lecture": 5}


KEYS = {"DeepSeek":"DEEPSEEK_API_KEY", "OpenRouter":"OPENROUTER_API_KEY", "RouterAI":"DEAN_AI_API_KEY"}


class ProviderFailure(DomainError):
    """A sanitized reason code for interfaces that offer a recovery action."""

    def __init__(self, status, message, reason):
        super().__init__(status, message)
        self.reason = reason


PROVIDER_HTTP_FAILURES = {
    401: "provider-credentials",
    402: "provider-balance",
    403: "provider-access",
    404: "provider-model",
    408: "provider-timeout",
    429: "provider-limit",
    504: "provider-timeout",
}


def provider_key(prefix):
    key = getenv(prefix, "").strip()
    key_file = getenv(prefix + "_FILE", "").strip()
    if not key and key_file:
        try:
            key = Path(key_file).read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            raise ValueError(f"Проверь файл {prefix}_FILE.") from None
        if not key:
            raise ValueError(f"Файл {prefix}_FILE пуст.")
    if len(key) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError(f"Проверь конфигурацию {prefix}.")
    return key


@dataclass(frozen=True)
class RouterAI:
    api_key: str = field(default="", repr=False)
    daily_limit: int = 100
    total_limit: int = 300
    connect: object = field(default=HTTPSConnection, repr=False)

    provider_name: str = "RouterAI"

    vision: object = field(default=None, repr=False)

    @classmethod
    def from_env(cls):
        selected = getenv("DEAN_AI_PROVIDER", "").strip().lower()
        names = {name.lower():name for name in KEYS}
        if selected and selected not in names:
            raise ValueError("DEAN_AI_PROVIDER принимает deepseek, openrouter или routerai.")
        name = names.get(selected)
        if not name:
            name = next((n for n,p in KEYS.items() if getenv(p) or getenv(p+"_FILE")), "RouterAI")
        key = provider_key(KEYS[name])
        try:
            limit = int(getenv("DEAN_AI_DAILY_LIMIT", "100"))
            total = int(getenv("DEAN_AI_TOTAL_LIMIT", "300"))
        except ValueError:
            limit, total = 0, 0
        if not (1 <= limit <= 500 and 1 <= total <= 100_000):
            raise ValueError("Проверь лимиты DEAN_AI_DAILY_LIMIT и DEAN_AI_TOTAL_LIMIT.")
        vision = None
        if name == "DeepSeek":
            for photo_provider in ("OpenRouter","RouterAI"):
                prefix = KEYS[photo_provider]
                if getenv(prefix) or getenv(prefix+"_FILE"):
                    vision = cls(provider_key(prefix),limit,total,provider_name=photo_provider)
                    break
        return cls(key, limit, total, provider_name=name, vision=vision)

    @property
    def enabled(self):
        return bool(self.api_key)

    @property
    def vision_enabled(self):
        return bool(self.vision) if self.provider_name == "DeepSeek" else self.enabled

    @property
    def vision_label(self):
        if self.vision:
            return self.vision.vision_label
        return "Qwen через RouterAI" if self.provider_name == "RouterAI" else self.provider_name

    def complete(self, messages, max_tokens, *, model=PHOTO_MODEL):
        if model == PHOTO_MODEL and self.provider_name == "DeepSeek":
            if self.vision:
                return self.vision.complete(messages,max_tokens,model=model)
            raise DomainError(503,"Для облачного фото подключи модель с поддержкой изображений.")
        if not self.enabled:
            raise DomainError(503, "Распознавание и модель пока не подключены.")
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "reasoning": {"enabled": False},
            "provider": {"only": ["deepinfra"], "allow_fallbacks": False},
            "response_format": {"type": "json_object"},
        }
        host, endpoint = HOST, "/api/v1/chat/completions"
        if model == CHAT_MODEL and self.provider_name == "RouterAI":
            payload["model"] = getenv("DEAN_ROUTERAI_CHAT_MODEL") or CHAT_MODEL
        if model == PHOTO_MODEL and self.provider_name == "RouterAI":
            payload.pop("provider")
        if self.provider_name == "DeepSeek":
            host, endpoint = "api.deepseek.com", "/chat/completions"
            payload["model"] = getenv("DEAN_DEEPSEEK_CHAT_MODEL") or "deepseek-flash"
            payload.pop("provider")
            payload.pop("reasoning")
            payload["thinking"] = {"type":"disabled"}
        if self.provider_name == "OpenRouter":
            host = "openrouter.ai"
            payload.pop("provider")
            payload.pop("reasoning")
            payload["model"] = ((getenv("DEAN_OPENROUTER_CHAT_MODEL") or "deepseek/deepseek-v3.2") if model == CHAT_MODEL
                                else (getenv("DEAN_OPENROUTER_VISION_MODEL") or "google/gemini-2.5-flash"))
        connection = self.connect(host, 443, timeout=TIMEOUT)
        try:
            connection.request("POST", endpoint, json.dumps(payload, ensure_ascii=False).encode(),
                               {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"})
            with connection.getresponse() as response:
                if response.status != 200:
                    raise ProviderFailure(503, "Сервис ИИ сейчас недоступен. Попробуй чуть позже.",
                                          PROVIDER_HTTP_FAILURES.get(response.status, "provider"))
                data = response.read(MAX_RESPONSE + 1)
                if len(data) > MAX_RESPONSE:
                    raise ValueError
            envelope = json.loads(data)
            choice = envelope["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter", "error"}:
                raise ValueError
            result = json.loads(choice["message"]["content"])
            if not isinstance(result, dict):
                raise ValueError
            return result
        except TimeoutError:
            raise ProviderFailure(503, "Сервис ИИ не ответил вовремя. Попробуй чуть позже.",
                                  "provider-timeout") from None
        except (HTTPException, OSError):
            raise ProviderFailure(503, "Сервис ИИ не ответил вовремя. Попробуй чуть позже.",
                                  "provider-network") from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError):
            raise ProviderFailure(502, "Не получилось прочитать ответ ИИ. Попробуй ещё раз.",
                                  "provider-format") from None
        finally:
            connection.close()


def once(db, user, engine, kind, key, material, run, *, cached=None):
    """Reserve capacity before a call, and retain an owner-scoped receipt for retry."""
    try:
        key = UUID(key).hex
    except (ValueError, TypeError, AttributeError):
        raise DomainError(422, "Открой форму заново и повтори действие.") from None
    owner = user["id"]
    current = now()
    stamp = timestamp(current)
    fingerprint = sha256(material).hexdigest()
    fields = (owner, kind, key)
    with write(db):
        previous = db.execute("""
            SELECT * FROM ai_request WHERE owner_id=? AND kind=? AND command_id=?
        """, fields).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise DomainError(409, "Для нового запроса открой свежую форму.")
            if previous["result_json"] is not None:
                return json.loads(previous["result_json"])
            if previous["started_at"] < timestamp(current-PENDING_RETRY_WINDOW):
                raise DomainError(409, "Ответ на предыдущую попытку не сохранился. Повтори отправку.")
            return {"status":409,"pending":True,
                    "error":"Ответ ещё готовится. Повтори отправку через несколько секунд."}
        if cached and (result := cached()) is not None:
            return result
        db.execute("DELETE FROM ai_request WHERE started_at<?", (timestamp(current-timedelta(days=2)),))
        midnight = stamp[:10] + "T00:00:00+00:00"
        total = db.execute("SELECT count(*) FROM ai_request WHERE started_at>=?", (midnight,)).fetchone()[0]
        account_midnight = midnight
        if kind == "assistant":
            local_time = current.astimezone(ZoneInfo(user["timezone"]))
            account_midnight = timestamp(local_time.replace(hour=0, minute=0, second=0, microsecond=0))
        mine = db.execute("""
            SELECT count(*) FROM ai_request WHERE owner_id=? AND kind=? AND started_at>=?
        """, (owner,kind,account_midnight)).fetchone()[0]
        if kind == "assistant" and mine >= ACCOUNT_LIMITS[kind]:
            raise DomainError(429, f"Твой дневной лимит Вишки исчерпан. Доступно {ACCOUNT_LIMITS[kind]} запросов в день.")
        if total >= engine.daily_limit or mine >= ACCOUNT_LIMITS[kind]:
            raise DomainError(429, "На сегодня лимит запросов к ИИ исчерпан.")
        reserved = db.execute("SELECT reserved FROM ai_budget WHERE id=1").fetchone()[0]
        if reserved >= engine.total_limit:
            raise DomainError(429, "Общий лимит запросов к ИИ исчерпан.")
        busy = db.execute("""
            SELECT count(*) FROM ai_request WHERE result_json IS NULL AND started_at>?
        """, (timestamp(current-timedelta(seconds=TIMEOUT+6)),)).fetchone()[0]
        if busy >= 2:
            raise DomainError(429, "Сейчас ИИ обрабатывает два запроса. Повтори позже.")
        db.execute("INSERT INTO ai_request VALUES (?,?,?,?,?,NULL)", (*fields,fingerprint,stamp))
        db.execute("UPDATE ai_budget SET reserved=reserved+1 WHERE id=1")
    try:
        result = run()
    except DomainError as error:
        result = {"error": error.message, "status": error.status}
    db.execute("""
        UPDATE ai_request SET result_json=? WHERE owner_id=? AND kind=? AND command_id=?
    """, (json.dumps(result,ensure_ascii=False),*fields))
    return result
