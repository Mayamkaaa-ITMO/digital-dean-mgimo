"""One-use login and linking state and the mapping to existing demo accounts."""
import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from . import auth
from .db import write
from .domain import DomainError, identifier, timestamp

ATTEMPT_TTL = timedelta(minutes=5)
INVITE_TTL = timedelta(minutes=30)
EXPIRED = "Срок действия попытки истёк. Попробуй ещё раз."
UNLINKED = "Этот способ пока не привязан. Войди привычным способом и добавь его в профиле."
UNLINKED_MAX = "Открой приглашение или войди привычным способом и привяжи MAX в профиле."
BUSY = "Способ входа уже привязан. Проверь, какой аккаунт выбран у провайдера."
INVITE_INVALID = "Приглашение истекло или уже использовано. Получи новое у организатора."


def subject_digest(subject):
    return hashlib.sha256(subject.encode("ascii")).hexdigest()


def invite(db, account_id):
    token = auth.token()
    with write(db):
        if not auth.personal(db, account_id):
            raise DomainError(422, "Для приглашения нужен индивидуальный демо-кабинет.")
        now = auth.real_now()
        db.execute("DELETE FROM max_invitation WHERE account_id=? OR expires_at<=?",
                   (account_id, timestamp(now)))
        db.execute("INSERT INTO max_invitation VALUES (?,?,?)",
                   (auth.digest(token), account_id, timestamp(now + INVITE_TTL)))
    return token


def begin(db, request, provider, intent, *, proof="", target="", remember=False, invitation=""):
    account = request.user["id"] if request.user and intent != "login" else None
    browser = request.session if account else request.cookie(request.login_cookie)
    if not auth.TOKEN.fullmatch(browser) or bool(account) != (intent != "login"):
        raise DomainError(403, "Открой страницу входа или свой профиль заново.")
    key = auth.token()
    exp = auth.real_now() + ATTEMPT_TTL
    invite_hash = None
    pin = f"{secrets.randbelow(1_000_000):06d}" if provider == "max" else None
    address = auth.digest(request.address)
    with write(db):
        now = timestamp(auth.real_now())
        db.execute("DELETE FROM identity_attempt WHERE expires_at<=?", (now,))
        if account:
            if not auth.personal(db, account):
                raise DomainError(403, "Этот способ доступен в индивидуальном демо-кабинете.")
            auth.require_session(db, browser, account, confirmed=intent == "link")
            if intent == "confirm" and provider != "key" and not linked(db, account, provider):
                raise DomainError(403, "Выбери уже привязанный способ.")
        if db.execute("SELECT COUNT(*) FROM identity_attempt WHERE address_hash=?", (address,)).fetchone()[0] >= 30:
            raise DomainError(429, "Слишком много попыток. Попробуй через пять минут.")
        if invitation:
            if provider != "max" or intent != "login" or not auth.TOKEN.fullmatch(invitation):
                raise DomainError(422, INVITE_INVALID)
            invite_hash = auth.digest(invitation)
            if not db.execute("SELECT 1 FROM max_invitation WHERE token_hash=? AND expires_at>?",
                              (invite_hash, now)).fetchone():
                raise DomainError(422, INVITE_INVALID)
        db.execute("""
            INSERT INTO identity_attempt(token_hash,provider,intent,browser_hash,address_hash,account_id,
                target,remember,proof,invitation_hash,confirmation_code,expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (auth.digest(key), provider, intent, auth.digest(browser), address, account,
              target, int(remember), proof, invite_hash, pin, timestamp(exp)))
    return key


def active(db, key, provider):
    if not auth.TOKEN.fullmatch(key):
        raise DomainError(410, EXPIRED)
    row = db.execute("""
        SELECT * FROM identity_attempt WHERE token_hash=? AND provider=? AND used=0 AND expires_at>?
    """, (auth.digest(key), provider, timestamp(auth.real_now()))).fetchone()
    if not row:
        raise DomainError(410, EXPIRED)
    return row


def owned(db, request, key, provider):
    row = active(db, key, provider)
    if row["account_id"]:
        auth.require_session(db, request.session, row["account_id"], confirmed=row["intent"] == "link")
        browser = request.session
    else:
        if request.user:
            raise DomainError(409, "В этом браузере уже открыт кабинет. Начни вход заново.")
        browser = request.cookie(request.login_cookie)
    if not hmac.compare_digest(row["browser_hash"], auth.digest(browser)):
        raise DomainError(403, "Заверши вход в браузере, в котором ты его начал.")
    return row


def consume(db, row):
    db.execute("""
        UPDATE identity_attempt SET used=1,proof='',subject_hash=NULL,confirmation_code=NULL WHERE token_hash=?
    """, (row["token_hash"],))


def require_claim(db, row):
    if not db.execute("SELECT 1 FROM identity_attempt WHERE token_hash=? AND used=1 AND expires_at>?",
                      (row["token_hash"], timestamp(auth.real_now()))).fetchone():
        raise DomainError(410, EXPIRED)


def linked(db, account, provider):
    return db.execute("SELECT 1 FROM identity_link WHERE account_id=? AND provider=?", (account, provider)).fetchone() is not None


def establish_link(db, account, provider, subject):
    rows = db.execute("""
        SELECT account_id,subject_hash FROM identity_link
        WHERE provider=? AND (subject_hash=? OR account_id=?)
    """, (provider, subject, account)).fetchall()
    if rows:
        if len(rows) == 1 and rows[0]["account_id"] == account and rows[0]["subject_hash"] == subject:
            return
        raise DomainError(409, BUSY)
    db.execute("INSERT INTO identity_link VALUES (?,?,?,?)", (provider, subject, account, timestamp(auth.real_now())))


def sign_in(db, request, row, account_id, method):
    account = db.execute("SELECT id,role FROM account WHERE id=? AND role='student'", (account_id,)).fetchone()
    if not account or not auth.personal(db, account_id):
        raise DomainError(403, "Кабинет недоступен.")
    db.execute("DELETE FROM identity_attempt WHERE browser_hash=? AND intent='login'", (row["browser_hash"],))
    return auth.sign_in(db, account, request.session, request.cookie(request.remember_cookie),
                        remember=bool(row["remember"]), method=method)


def confirm_session(db, request, account_id):
    db.execute("UPDATE session SET confirmed_at=? WHERE token_hash=? AND account_id=?",
               (timestamp(auth.real_now()), auth.digest(request.session), account_id))


def complete_identity(db, request, row, subject):
    """Called with a write lock after validating the provider and consuming state."""
    provider = row["provider"]
    account = row["account_id"]
    require_claim(db, row)
    if row["intent"] == "link":
        auth.require_session(db, request.session, account, confirmed=True)
        if not auth.personal(db, account):
            raise DomainError(403, "Этот способ доступен в индивидуальном демо-кабинете.")
        establish_link(db, account, provider, subject)
        return None

    link = db.execute("SELECT account_id FROM identity_link WHERE provider=? AND subject_hash=?",
                      (provider, subject)).fetchone()
    if row["intent"] == "confirm":
        auth.require_session(db, request.session, account)
        if not link or link["account_id"] != account:
            raise DomainError(403, "Подтверди вход способом, привязанным к этому кабинету.")
        confirm_session(db, request, account)
        return None

    if row["invitation_hash"]:
        invitation = db.execute("SELECT account_id FROM max_invitation WHERE token_hash=? AND expires_at>?",
                                (row["invitation_hash"], timestamp(auth.real_now()))).fetchone()
        if not invitation:
            raise DomainError(410, INVITE_INVALID)
        account = invitation["account_id"]
        establish_link(db, account, provider, subject)
        db.execute("DELETE FROM max_invitation WHERE token_hash=?", (row["invitation_hash"],))
    elif link:
        account = link["account_id"]
    else:
        raise DomainError(403, UNLINKED_MAX if provider == "max" else UNLINKED)
    return sign_in(db, request, row, account, provider)


def approval(db, key, pin, subject):
    if not re.fullmatch(r"[0-9]{6}", pin):
        raise DomainError(422, "Введи шесть цифр со страницы входа.")
    correct = False
    with write(db):
        row = active(db, key, "max")
        if row["subject_hash"]:
            raise DomainError(409, "Вход уже подтверждён. Вернись в исходный браузер.")
        if row["checks"] >= 5:
            raise DomainError(429, "Начни вход заново, чтобы получить новый код.")
        db.execute("UPDATE identity_attempt SET checks=checks+1 WHERE token_hash=?", (row["token_hash"],))
        correct = hmac.compare_digest(row["confirmation_code"], pin)
        if correct:
            db.execute("UPDATE identity_attempt SET subject_hash=? WHERE token_hash=?", (subject, row["token_hash"]))
    if not correct:
        raise DomainError(422, "Код не подошёл. Проверь шесть цифр со страницы входа.")


def can_remove(db, request, provider, credential=None):
    account = request.user["id"]
    row = db.execute("SELECT password_used FROM personal_login WHERE account_id=?", (account,)).fetchone()
    if row and row["password_used"]:
        return
    for item in db.execute("SELECT provider FROM identity_link WHERE account_id=? AND provider<>?", (account, provider)):
        if request.access.enabled(item["provider"], request.origin):
            return
    if request.access.enabled("key", request.origin) and db.execute("""
        SELECT 1 FROM access_key WHERE account_id=? AND id IS NOT ?
    """, (account, credential)).fetchone():
        return
    raise DomainError(422, "Сначала войди по паролю или подключи ещё один способ входа.")


def remove(db, request, provider, credential=None):
    account = request.user["id"]
    with write(db):
        auth.require_session(db, request.session, account, confirmed=True)
        can_remove(db, request, provider, credential)
        if credential:
            deleted = db.execute("DELETE FROM access_key WHERE id=? AND account_id=?", (credential, account))
            method = "key:" + credential
        else:
            deleted = db.execute("DELETE FROM identity_link WHERE provider=? AND account_id=?", (provider, account))
            method = provider
        if not deleted.rowcount:
            raise DomainError(404, "Способ входа уже удалён.")
        db.execute("DELETE FROM trusted_device WHERE account_id=? AND method=?", (account, method))
        db.execute("DELETE FROM session WHERE account_id=? AND method=?", (account, method))
        db.execute("DELETE FROM identity_attempt WHERE account_id=? AND provider=?", (account, provider))
        return auth.active_session(db, request.session, auth.real_now()) is not None


def new_demo_account(db, username, password, *, from_login="student-a"):
    if not re.fullmatch(r"demo-[a-z0-9-]{1,48}", username):
        raise DomainError(422, "Используй логин вида demo-имя латиницей, например demo-personal.")
    stored = auth.hash_password(password)
    with write(db):
        if db.execute("SELECT 1 FROM account WHERE username=?", (username,)).fetchone():
            raise DomainError(409, "Логин уже занят. Укажи новый.")
        context = db.execute("""
            SELECT university_id,group_id,period_id FROM account WHERE username=? AND role='student'
        """, (from_login,)).fetchone()
        if not context:
            raise DomainError(404, "Для создания демо-кабинета нужен исходный аккаунт student-a.")
        account = identifier()
        db.execute("INSERT INTO account VALUES (?,?,?,?,?,?,?,?)",
                   (account, username, stored, "Студент Демо", "student", *context))
        db.execute("INSERT INTO personal_login(account_id) VALUES (?)", (account,))
