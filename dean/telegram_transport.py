"""Minimal Telegram Bot API transport with sanitized results."""
from dataclasses import dataclass, field
from http.client import HTTPSConnection, HTTPException
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from .config import WebConfig
from .domain import DomainError

USERNAME=re.compile(r"[A-Za-z0-9_]{5,32}")
BOT_TOKEN=re.compile(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,90}")
METHODS={"getMe","getWebhookInfo","getUpdates","sendMessage","setMyName","setMyDescription",
         "setMyShortDescription","setMyCommands","setMyProfilePhoto","setChatMenuButton"}
MAX_RESPONSE=1024*1024


def bot_token():
    token = os.getenv("DEAN_TELEGRAM_BOT_TOKEN", "").strip()
    token_file = os.getenv("DEAN_TELEGRAM_BOT_TOKEN_FILE", "").strip()
    if not token and token_file:
        try:
            token = Path(token_file).read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            raise DomainError(422, "Проверь файл DEAN_TELEGRAM_BOT_TOKEN_FILE.") from None
    if not BOT_TOKEN.fullmatch(token):
        raise DomainError(422, "Задай токен Telegram через DEAN_TELEGRAM_BOT_TOKEN или DEAN_TELEGRAM_BOT_TOKEN_FILE.")
    return token


@dataclass(frozen=True)
class TelegramConfig:
    mode: str = "off"
    name: str = ""
    token: str = field(default="",repr=False)
    allowed: frozenset = frozenset()
    site: str = ""
    public: bool = False

    def permits(self, chat):
        return type(chat) is int and 0 < chat < 2**53 and (self.public or chat in self.allowed)

    @classmethod
    def from_env(cls, *, worker=False):
        mode=os.getenv("DEAN_TELEGRAM_MODE","off")
        if mode=="off" and not worker:
            return cls()
        if mode!="polling":
            raise DomainError(422,"Для Telegram укажи явно DEAN_TELEGRAM_MODE=polling.")
        name=os.getenv("DEAN_TELEGRAM_BOT_USERNAME","").strip().lower()
        if not USERNAME.fullmatch(name):
            raise DomainError(422,"Укажи имя Telegram-бота без @.")
        raw_allowed=os.getenv("DEAN_TELEGRAM_ALLOWED_CHAT_IDS","")
        public=os.getenv("DEAN_TELEGRAM_PUBLIC","0")
        if public not in {"0","1"}:
            raise DomainError(422,"DEAN_TELEGRAM_PUBLIC принимает 0 или 1.")
        parts=raw_allowed.split(",") if raw_allowed else []
        if (len(parts)>20 or any(not re.fullmatch(r"[0-9]{1,17}",value.strip()) for value in parts)):
            raise DomainError(422,"Список тестовых чатов должен содержать положительные числовые идентификаторы.")
        allowed=frozenset(int(value) for value in parts)
        if any(value<=0 or value>=2**53 for value in allowed) or worker and not allowed and public=="0":
            raise DomainError(422,"Укажи тестовый чат или явно разреши личные чаты.")
        token=bot_token() if worker else ""
        site=os.getenv("DEAN_TELEGRAM_APP_URL","")
        if site:
            try:
                WebConfig(site)
                parsed=urlsplit(site)
                try:
                    ip=ipaddress.ip_address(parsed.hostname)
                except ValueError:
                    ip=None
                if parsed.hostname=="localhost" or parsed.hostname.endswith(".local") or ip is not None and not ip.is_global:
                    raise ValueError
            except ValueError:
                raise DomainError(422,"Адрес кабинета должен быть внешним HTTPS-адресом.") from None
        return cls(mode,name,token,allowed,site,public=="1")


class TransportError(Exception):
    def __init__(self,code,retry_after=None):
        super().__init__(code)
        self.code=code
        self.retry_after=retry_after


class BotAPI:
    def __init__(self,config,connect=HTTPSConnection):
        self.config=config
        self.connect=connect

    def call(self,method,payload,*,avatar=None):
        if method not in METHODS:
            raise ValueError("Unsupported Telegram method")
        connection=self.connect("api.telegram.org",443,timeout=25)
        try:
            raw=json.dumps(payload,ensure_ascii=False).encode()
            content_type="application/json"
            if avatar is not None:
                boundary="dean-" + uuid4().hex
                content_type=f"multipart/form-data; boundary={boundary}"
                raw=(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"\r\n\r\n'.encode()
                     +raw+f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="avatar"; '
                     'filename="vishka.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
                     +avatar+f"\r\n--{boundary}--\r\n".encode())
            connection.request("POST",f"/bot{self.config.token}/{method}",raw,{"Content-Type":content_type})
            with connection.getresponse() as response:
                body=response.read(MAX_RESPONSE+1)
                if len(body)>MAX_RESPONSE:
                    raise TransportError("uncertain" if method=="sendMessage" else "invalid_response")
                try:
                    data=json.loads(body)
                except (ValueError,UnicodeError,RecursionError):
                    raise TransportError("uncertain" if method=="sendMessage" else "invalid_response") from None
                if not isinstance(data,dict):
                    raise TransportError("uncertain" if method=="sendMessage" else "invalid_response")
                if response.status==200 and data.get("ok") is True:
                    return data.get("result")
                code=data.get("error_code",response.status)
                if code==429:
                    params=data.get("parameters")
                    retry=params.get("retry_after") if isinstance(params,dict) else None
                    raise TransportError("rate_limit", min(86400,max(1,retry)) if type(retry) is int else 60)
                if code in (401,403):
                    raise TransportError("blocked" if code==403 else "credentials")
                if code==409 and method=="getUpdates":
                    raise TransportError("poll_conflict")
                if type(code) is int and 400<=code<500:
                    raise TransportError("rejected")
                raise TransportError("uncertain" if method=="sendMessage" else "unavailable")
        except (HTTPException,OSError,ValueError):
            raise TransportError("uncertain" if method=="sendMessage" else "unavailable") from None
        finally:
            connection.close()

    def preflight(self):
        hook=self.call("getWebhookInfo",{})
        if not isinstance(hook,dict) or not isinstance(hook.get("url"),str):
            raise TransportError("invalid_response")
        if hook["url"]:
            raise TransportError("webhook_already_set")
        identity=self.call("getMe",{})
        name=identity.get("username") if isinstance(identity,dict) and identity.get("is_bot") is True else None
        if not isinstance(name,str) or not USERNAME.fullmatch(name):
            raise TransportError("invalid_response")
        if self.config.name and name.lower()!=self.config.name:
            raise TransportError("bot_identity_mismatch")
        return name.lower()

    def updates(self,offset):
        result=self.call("getUpdates",{"offset":offset,"limit":20,"timeout":10,"allowed_updates":["message"]})
        if not isinstance(result,list) or len(result)>20:
            raise TransportError("invalid_response")
        return result

    def configure(self):
        commands={"start":"Подключить кабинет", "today":"Расписание на сегодня",
                  "week":"Расписание на неделю", "tasks":"Мои задачи", "requests":"Статусы заявок",
                  "cabinet":"Открыть кабинет", "settings":"Настройки Telegram", "help":"Помощь",
                  "stop":"Отключить кабинет"}
        changes=(
            ("setMyName", {"name":"Вишка · Цифровой деканат"}),
            ("setMyDescription", {"description":"Привет, я Вишка! Покажу расписание, задачи и статусы заявок. "
                                                "Подключи кабинет, чтобы получать уведомления в личном чате."}),
            ("setMyShortDescription", {"short_description":"Расписание, задачи и уведомления Цифрового деканата."}),
            ("setMyCommands", {"commands":[{"command":name,"description":label} for name,label in commands.items()]}),
            ("setChatMenuButton", {"menu_button":{"type":"commands"}}),
        )
        for method,payload in changes:
            if self.call(method,payload) is not True:
                raise TransportError("invalid_response")
        avatar=(Path(__file__).with_name("static") / "telegram-avatar.jpg").read_bytes()
        if self.call("setMyProfilePhoto", {"type":"static","photo":"attach://avatar"}, avatar=avatar) is not True:
            raise TransportError("invalid_response")

    def send(self,chat,text,*,reply_markup=None):
        body={"chat_id":chat,"text":text,"link_preview_options":{"is_disabled":True}}
        if reply_markup:
            body["reply_markup"]=reply_markup
        result=self.call("sendMessage",body)
        if not isinstance(result,dict) or type(result.get("message_id")) is not int:
            raise TransportError("uncertain")
        return result["message_id"]
