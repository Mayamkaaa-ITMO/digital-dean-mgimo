"""Validate signed MAX Bridge startup data on the server."""
import hmac
import json
import re
from urllib.parse import parse_qsl, urlencode

from . import access_state, auth, oauth
from .domain import DomainError

BRIDGE = "https://st.max.ru/js/max-web-app.js"
FAIL = "MAX не подтвердил эти данные. Открой мини-приложение по новой ссылке со страницы входа."


def url(settings, state):
    return f"https://max.ru/{settings.max_name}?" + urlencode({"startapp": state})


def verify(init_data, bot_token):
    try:
        if not init_data or len(init_data) > 8192:
            raise ValueError
        pairs = parse_qsl(init_data, keep_blank_values=True, max_num_fields=16, strict_parsing=True, errors="strict")
        fields = dict(pairs)
        if len(fields) != len(pairs) or any(not re.fullmatch(r"[a-zA-Z0-9_]+", key) or "\n" in value or "\r" in value
                                            for key, value in pairs):
            raise ValueError
        provided = fields.pop("hash", "")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", provided):
            raise ValueError
        serialized = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.digest(b"WebAppData", bot_token.encode(), "sha256")
        if not hmac.compare_digest(hmac.digest(secret, serialized.encode(), "sha256").hex(), provided.lower()):
            raise ValueError
        date = fields["auth_date"]
        state = fields["start_param"]
        if not re.fullmatch(r"[0-9]{1,12}", date) or not auth.TOKEN.fullmatch(state):
            raise ValueError
        if not -30 <= auth.real_now().timestamp() - int(date) <= 300:
            raise ValueError
        user = json.loads(fields["user"])
        if not isinstance(user, dict):
            raise ValueError
        return state, access_state.subject_digest(oauth.subject_id(user["id"]))
    except (ValueError, KeyError, DomainError):
        raise DomainError(403, FAIL) from None
