"""Server exchanges for the two independently configured authorization-code flows."""
import base64
import hashlib
import hmac
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from time import sleep
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from .domain import DomainError

VK = "https://id.vk.ru"
YANDEX = "https://oauth.yandex.ru"
FAIL = "Провайдер пока не подтвердил вход. Попробуй ещё раз или войди по паролю."
LOG = logging.getLogger(__name__)


def callback(origin, provider):
    return f"{origin}/auth/{provider}/callback"


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")


def authorize(origin, settings, provider, state, verifier, intent):
    params = {
        "redirect_uri": callback(origin, provider), "response_type": "code",
        "state": state, "code_challenge": challenge(verifier),
    }
    if provider == "vk":
        endpoint = VK
        params.update(client_id=settings.vk_client, code_challenge_method="s256", scope="")
        if intent != "login":
            params["prompt"] = "login"
    else:
        endpoint = YANDEX
        params.update(client_id=settings.yandex_client, code_challenge_method="S256",
                      scope="login:info", force_confirm="yes")
    return endpoint + "/authorize?" + urlencode(params)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def mac_vk_response(uri, data):
    # Use macOS's system TLS/HTTP stack. Form data travels over stdin only.
    args = ["/usr/bin/curl", "--disable", "--silent", "--proto", "=https",
            "--connect-timeout", "4", "--max-time", "9", "--max-filesize", "65536",
            "--header", "Accept: application/json", "--data-binary", "@-",
            "--write-out", "\\n%{http_code} %{time_appconnect} %{size_upload}", "--url", uri]
    for attempt in range(3):
        result = subprocess.run(args, input=data, capture_output=True, timeout=11)
        body, _, stats = result.stdout.rpartition(b"\n")
        status, handshake, uploaded = stats.split()
        if result.returncode == 0:
            if status == b"200":
                return body
            LOG.warning("VK %s: HTTP %s", urlsplit(uri).path, status.decode("ascii"))
            raise DomainError(502, FAIL)
        # Retrying is safe only before TLS completed and before any form bytes
        # were uploaded. Never replay a code whose exchange may have succeeded.
        before_request = (result.returncode in {5, 6, 7, 28, 35, 52, 55, 56}
                          and status == b"000" and float(handshake) == 0 and int(uploaded) == 0)
        LOG.warning("VK %s: curl %d, before_request=%s", urlsplit(uri).path, result.returncode, before_request)
        if not before_request or attempt == 2:
            raise DomainError(502, FAIL)
        sleep(.2 * (attempt + 1))


def provider_json(uri, fields=None, authorization=None):
    headers = {"Accept": "application/json"}
    if authorization:
        headers["Authorization"] = authorization
    data = urlencode(fields).encode("ascii") if fields else None
    request = urllib.request.Request(uri, data=data, headers=headers)
    opener = urllib.request.build_opener(NoRedirect)
    endpoint = urlsplit(uri)
    try:
        if sys.platform == "darwin" and uri.startswith(VK + "/oauth2/") and Path("/usr/bin/curl").is_file():
            body = mac_vk_response(uri, data)
        else:
            with opener.open(request, timeout=6) as result:
                body = result.read(65_537)
        if len(body) > 65_536:
            raise ValueError
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
        if "error" in payload:
            tag = payload["error"]
            safe_tag = tag if tag in ("invalid_request", "invalid_grant", "invalid_token",
                                      "invalid_client", "access_denied", "server_error") else "provider_error"
            LOG.warning("OAuth %s%s: %s", endpoint.netloc, endpoint.path, safe_tag)
            raise DomainError(502, FAIL)
        return payload
    except urllib.error.HTTPError as error:
        LOG.warning("OAuth %s%s: HTTP %d", endpoint.netloc, endpoint.path, error.code)
        error.close()
        raise DomainError(502, FAIL) from None
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        LOG.warning("OAuth %s%s: %s", endpoint.netloc, endpoint.path,
                    type(getattr(error, "reason", error)).__name__)
        raise DomainError(502, FAIL) from None


def get_token(reply):
    token = reply.get("access_token")
    if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,8192}", token):
        raise DomainError(502, FAIL)
    return token


def subject_id(subject):
    if type(subject) is int:
        subject = str(subject)
    if not isinstance(subject, str) or not re.fullmatch(r"[1-9][0-9]{0,30}", subject):
        raise DomainError(502, FAIL)
    return subject


def exchange(origin, settings, provider, code, state, verifier, device):
    if not re.fullmatch(r"[!-~]{1,2048}", code):
        raise DomainError(422, FAIL)
    params = {"grant_type": "authorization_code", "code_verifier": verifier,
              "redirect_uri": callback(origin, provider)}
    if provider == "vk":
        if not re.fullmatch(r"[!-~]{1,512}", device):
            raise DomainError(422, FAIL)
        params.update(client_id=settings.vk_client, state=state, device_id=device, code=code)
        token = provider_json(VK + "/oauth2/auth", params)
        returned = token.get("state")
        if not isinstance(returned, str) or not returned.isascii() or not hmac.compare_digest(state, returned):
            raise DomainError(502, FAIL)
        access_token = get_token(token)
        # VK supplies the account ID directly in the server-to-server PKCE exchange.
        if "user_id" in token:
            return subject_id(token["user_id"])
        info = provider_json(VK + "/oauth2/user_info",
                             {"client_id": settings.vk_client, "access_token": access_token}).get("user")
        return subject_id(info.get("user_id") if isinstance(info, dict) else None)

    params.update(code=code, client_id=settings.yandex_client, client_secret=settings.yandex_secret)
    token = get_token(provider_json(YANDEX + "/token", params))
    info = provider_json("https://login.yandex.ru/info?format=json", authorization="OAuth " + token)
    if info.get("client_id") != settings.yandex_client:
        raise DomainError(502, FAIL)
    return subject_id(info.get("id"))
