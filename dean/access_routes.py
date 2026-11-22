"""Authentication endpoints for the named demo accounts."""
import json
import re
from urllib.parse import urlencode

from . import access_paths, access_state, access_views, auth, max_access, oauth, passkeys, web
from .access_config import SOCIAL
from .db import write
from .domain import DomainError, identifier, timestamp

METHODS = access_views.METHODS
DISMISS = access_views.DISMISS
PROVIDERS = ("max", "vk", "yandex")
PROFILE_NOTICES = {
    "linked": "Способ входа привязан к твоему кабинету.",
    "confirmed": "Вход подтверждён. Теперь можно изменить способы входа.",
    "removed": "Способ входа удалён.",
    "cancelled": "Действие отменено. Привязки сохранены.",
    "provider": oauth.FAIL,
}


def json_response(data, status=200):
    return status, json.dumps(data, ensure_ascii=False), [("Content-Type", "application/json; charset=utf-8")]


def check_method(request, *methods):
    if request.method not in methods:
        raise DomainError(405, "Метод запроса недоступен.")


def require_enabled(request, provider):
    if not request.access.enabled(provider, request.origin):
        raise DomainError(404, "Этот способ ещё не подключён на стенде.")


def require_personal(request, db):
    if not request.user or request.user["role"] != "student" or not auth.personal(db, request.user["id"]):
        raise DomainError(403, "Этот способ доступен в индивидуальном демо-кабинете.")
    return request.user["id"]


def attempt_form(request, db, provider, allowed):
    # Locate the CSRF bearer from the authenticated context, before reading any submitted field.
    browser = request.session if request.user else request.cookie(request.login_cookie)
    request.read_form(browser)
    intent = request.field("intent") if request.user else "login"
    if intent not in allowed or (intent == "login") != (request.user is None):
        raise DomainError(403, "Начни вход со страницы входа, а привязку — со своего профиля.")
    if request.user:
        require_personal(request, db)
    require_enabled(request, provider)
    return intent


def setup_target(value, enabled):
    return (access_paths.destination(value) or "/") if enabled == "1" else ""


def methods_url(notice, target):
    args = {"notice": notice}
    if target:
        args.update(setup="1", next=target)
    return METHODS + "?" + urlencode(args)


def offer(request, db):
    user = request.user
    if user["role"] != "student" or not auth.recent_confirmation(user):
        return None
    enabled = tuple(provider for provider in SOCIAL if request.access.enabled(provider, request.origin))
    if not enabled or not auth.personal(db, user["id"]) or db.execute(
        "SELECT 1 FROM access_intro_dismissal WHERE account_id=?", (user["id"],)
    ).fetchone():
        return None
    providers = tuple(provider for provider in enabled if not access_state.linked(db, user["id"], provider))
    if providers:
        query = request.env.get("QUERY_STRING", "")
        target = request.path + ("?" + query if query else "")
        return access_paths.destination(target) or "/", providers
    return None


def dismiss_offer(request, db):
    check_method(request, "POST")
    account = require_personal(request, db)
    request.read_form(request.session)
    with write(db):
        auth.require_session(db, request.session, account)
        db.execute("INSERT INTO access_intro_dismissal VALUES (?,?) ON CONFLICT(account_id) DO NOTHING",
                   (account, timestamp()))
    return web.redirect(access_paths.destination(request.field("next")) or "/")


def profile(request, db, *, message="", status=200, setup=None):
    if not request.user or request.user["role"] != "student":
        raise DomainError(403, "Открой профиль студента.")
    account = request.user["id"]
    methods = [row["provider"] for row in db.execute("""
        SELECT provider FROM identity_link WHERE account_id=? ORDER BY provider
    """, (account,))]
    keys = db.execute("SELECT id,created_at FROM access_key WHERE account_id=? ORDER BY created_at DESC",
                      (account,)).fetchall()
    feedback = message or PROFILE_NOTICES.get(request.query("notice"), "")
    if setup is None:
        setup = setup_target(request.query("next"), request.query("setup"))
    is_error = status >= 400 or request.query("notice") == "provider"
    return web.render(access_views.profile(request, methods, keys, auth.personal(db, account),
                                           feedback, is_error, setup), status)


def confirm_password(request, db):
    account = require_personal(request, db)
    request.read_form(request.session)
    target = setup_target(request.field("next"), request.field("setup"))
    try:
        verified = auth.verify_password(db, request.user["username"], request.field("password"), request.address)
        with write(db):
            auth.require_session(db, request.session, account)
            stored = db.execute("SELECT password_hash FROM account WHERE id=?", (account,)).fetchone()[0]
            if stored != verified["password_hash"]:
                raise DomainError(403, "Пароль изменился. Войди заново.")
            db.execute("UPDATE personal_login SET password_used=1 WHERE account_id=?", (account,))
            access_state.confirm_session(db, request, account)
    except DomainError as error:
        return profile(request, db, message=error.message, status=error.status, setup=target)
    return web.redirect(methods_url("confirmed", target))


def failure(request, db, row, error):
    if (row and row["intent"] != "login") or (not row and request.user and request.user["role"] == "student"):
        return profile(request, db, message=error.message, status=error.status, setup=row["target"] if row else "")
    return web.render(web.login_page(request, row["target"] if row else "", message=error.message), error.status)


def start(request, db, provider):
    check_method(request, "POST")
    intent = attempt_form(request, db, provider, ("login", "link", "confirm"))
    target = (access_paths.destination(request.field("next")) if intent == "login"
              else setup_target(request.field("next"), request.field("setup")) if provider in SOCIAL else "")
    proof = auth.token() if provider != "max" else ""
    invitation = request.field("invitation") if provider == "max" else ""
    try:
        state = access_state.begin(db, request, provider, intent, proof=proof, target=target,
                                   remember=request.field("remember") == "1", invitation=invitation)
    except DomainError as error:
        attempt = {"target": target, "intent": intent}
        return failure(request, db, attempt, error)
    if provider == "max":
        return web.redirect(f"/auth/max/wait/{state}")
    return web.redirect(oauth.authorize(request.origin, request.access, provider, state, proof, intent))


def result(request, row, signed):
    if not signed:
        return web.redirect(methods_url("confirmed" if row["intent"] == "confirm" else "linked", row["target"]))
    web.set_login(request, signed)
    return web.redirect(access_paths.for_role(row["target"], signed.role) or web.home({"role": signed.role}))


def single_query(request, name):
    values = request.params.get(name, [])
    if len(values) > 1:
        raise DomainError(422, oauth.FAIL)
    return values[0] if values else ""


def callback(request, db, provider):
    check_method(request, "GET")
    require_enabled(request, provider)
    row = None
    try:
        state = single_query(request, "state")
        with write(db):
            row = access_state.owned(db, request, state, provider)
            access_state.consume(db, row)
        if single_query(request, "error"):
            return web.redirect(methods_url("cancelled", row["target"]) if row["intent"] != "login"
                                else access_paths.login_url(row["target"], "cancelled"))
        raw_subject = oauth.exchange(request.origin, request.access, provider, single_query(request, "code"),
                                     state, row["proof"], single_query(request, "device_id"))
        with write(db):
            signed = access_state.complete_identity(db, request, row, access_state.subject_digest(raw_subject))
    except DomainError as error:
        if row is not None and error.status == 502:
            return web.redirect(access_paths.login_url(row["target"], "provider") if row["intent"] == "login"
                                else methods_url("provider", row["target"]))
        return failure(request, db, row, error)
    return result(request, row, signed)


def unlink(request, db, provider, key=None):
    check_method(request, "POST")
    require_personal(request, db)
    request.read_form(request.session)
    if not auth.recent_confirmation(request.user):
        return profile(request, db, message="Сначала подтверди вход.", status=403)
    try:
        if request.field("confirm") != "1":
            return web.render(access_views.remove(request, provider, request.path))
        stays = access_state.remove(db, request, provider, key)
    except DomainError as error:
        return profile(request, db, message=error.message, status=error.status)
    if stays:
        return web.redirect(METHODS + "?notice=removed")
    request.set_cookie(request.session_cookie, "", 0)
    request.set_cookie(request.remember_cookie, "", 0)
    return web.redirect(access_paths.login_url(notice="removed"))


def max_wait(request, db, state, action):
    require_enabled(request, "max")
    presence = None
    try:
        if not action:
            check_method(request, "GET")
            presence = access_state.owned(db, request, state, "max")
            request.csrf = auth.csrf_token(request.session if presence["account_id"] else request.cookie(request.login_cookie))
            return web.render(access_views.wait_max(request, state, presence))
        check_method(request, "POST")
        request.read_form(request.session if request.user else request.cookie(request.login_cookie))
        with write(db):
            presence = access_state.owned(db, request, state, "max")
            if action != "cancel" and not presence["subject_hash"]:
                request.csrf = request.field("csrf")
                return web.render(access_views.wait_max(request, state, presence, "Ждём подтверждение в MAX."))
            access_state.consume(db, presence)
        if action == "cancel":
            return web.redirect(METHODS + "?notice=cancelled" if presence["intent"] != "login"
                                else access_paths.login_url(presence["target"], "cancelled"))
        with write(db):
            signed = access_state.complete_identity(db, request, presence, presence["subject_hash"])
        return result(request, presence, signed)
    except DomainError as error:
        return failure(request, db, presence, error)


def max_open(request):
    check_method(request, "GET")
    require_enabled(request, "max")
    request.user = None
    return web.render(access_views.max_miniapp(request))


def max_approval(request, db):
    require_enabled(request, "max")
    check_method(request, "POST")
    request.read_form(None)
    state, subject = max_access.verify(request.field("init_data"), request.access.max_token)
    access_state.approval(db, state, request.field("pin"), subject)
    return json_response({"return": request.origin + "/auth/max/wait/" + state})


def key_start(request, db):
    check_method(request, "POST")
    intent = attempt_form(request, db, "key", ("login", "link", "confirm"))
    target = access_paths.destination(request.field("next")) if intent == "login" else ""
    challenge = auth.token()
    existing = []
    if request.user:
        existing = [row["credential_id"] for row in db.execute("SELECT credential_id FROM access_key WHERE account_id=?",
                                                               (request.user["id"],))]
        if intent == "link" and len(existing) >= 10:
            raise DomainError(409, "Сначала удали один из десяти сохранённых ключей.")
        if intent == "confirm" and not existing:
            raise DomainError(422, "Подтверди вход уже подключённым способом.")
    state = access_state.begin(db, request, "key", intent, proof=challenge, target=target,
                               remember=request.field("remember") == "1")
    settings = passkeys.options(request.origin, challenge, intent, request.user, existing)
    return json_response({"state": state, "publicKey": settings})


def key_finish(request, db):
    check_method(request, "POST")
    require_enabled(request, "key")
    request.read_form(request.session if request.user else request.cookie(request.login_cookie))
    with write(db):
        row = access_state.owned(db, request, request.field("state"), "key")
        access_state.consume(db, row)
    credential = passkeys.payload(request.field("credential"))
    with write(db):
        access_state.require_claim(db, row)
        account = row["account_id"]
        if account:
            auth.require_session(db, request.session, account, confirmed=row["intent"] == "link")
        if row["intent"] == "link":
            credential_id, public_key, count = passkeys.registration(request.origin, row, credential)
            if db.execute("SELECT 1 FROM access_key WHERE credential_id=?", (credential_id,)).fetchone():
                raise DomainError(409, "Этот ключ уже зарегистрирован.")
            if db.execute("SELECT COUNT(*) FROM access_key WHERE account_id=?", (account,)).fetchone()[0] >= 10:
                raise DomainError(409, "Сначала удали один из десяти сохранённых ключей.")
            db.execute("INSERT INTO access_key VALUES (?,?,?,?,?,?)",
                       (identifier(), credential_id, public_key, account, count, timestamp(auth.real_now())))
            return json_response({"return": METHODS + "?notice=linked"})

        key = db.execute("SELECT * FROM access_key WHERE credential_id=?", (credential["id"],)).fetchone()
        if not key or account and account != key["account_id"]:
            raise DomainError(403, passkeys.FAIL)
        count = passkeys.authentication(request.origin, row, credential, key)
        db.execute("UPDATE access_key SET sign_count=? WHERE id=?", (count, key["id"]))
        if row["intent"] == "confirm":
            access_state.confirm_session(db, request, account)
            return json_response({"return": METHODS + "?notice=confirmed"})
        signed = access_state.sign_in(db, request, row, key["account_id"], "key:" + key["id"])
    web.set_login(request, signed)
    target = access_paths.for_role(row["target"], signed.role) or web.home({"role": signed.role})
    return json_response({"return": target})


def dispatch(request, db):
    path = request.path
    if path == DISMISS:
        return dismiss_offer(request, db)
    if path in (METHODS, METHODS + "/confirm"):
        if not request.user and request.method == "GET":
            return web.redirect(access_paths.login_url(METHODS))
        if path == METHODS:
            check_method(request, "GET")
            return profile(request, db)
        check_method(request, "POST")
        return confirm_password(request, db)

    if path == "/auth/max/invite":
        check_method(request, "GET")
        require_enabled(request, "max")
        if request.user:
            raise DomainError(409, "Выйди из открытого кабинета и снова открой приглашение.")
        return web.render(web.login_page(request, invitation=True))
    if path == "/auth/max/miniapp":
        return max_open(request)
    json_handlers = {"/auth/max/approve": max_approval, "/auth/key/start": key_start, "/auth/key/finish": key_finish}
    if path in json_handlers:
        try:
            return json_handlers[path](request, db)
        except DomainError as error:
            return json_response({"error": error.message}, error.status)
    wait = re.fullmatch(r"/auth/max/wait/([A-Za-z0-9_-]{43})(?:/(finish|cancel))?", path)
    if wait:
        return max_wait(request, db, *wait.groups())
    external = re.fullmatch(r"/auth/(max|vk|yandex)/(start|callback|unlink)", path)
    if external:
        provider, action = external.groups()
        if action == "start":
            return start(request, db, provider)
        if action == "unlink":
            return unlink(request, db, provider)
        if provider != "max":
            return callback(request, db, provider)
    key = re.fullmatch(r"/auth/key/([a-f0-9]{32})/unlink", path)
    if key:
        return unlink(request, db, "key", key[1])
    raise DomainError(404, "Этот способ входа недоступен.")
