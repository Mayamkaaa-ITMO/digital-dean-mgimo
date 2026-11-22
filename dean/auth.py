import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db import write
from .domain import DomainError, identifier, timestamp

SESSION_TTL = timedelta(hours=8)
SESSION_IDLE = timedelta(minutes=30)
CONFIRM_TTL = timedelta(minutes=10)
DEVICE_TTL = timedelta(days=14)
ROTATION_GRACE = timedelta(seconds=12)
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
DUMMY_HASH = "00" * 16 + "$" + "00" * 64


@dataclass(frozen=True)
class SignIn:
    bearer: str
    role: str
    remember: str | None = None
    remember_expires: str | None = None


def real_now():
    return datetime.now(UTC)


def token():
    return secrets.token_urlsafe(32)


def hash_password(password, salt=None):
    salt = secrets.token_bytes(16) if salt is None else salt
    value = hashlib.scrypt(
        password.encode(), salt=salt, n=2**17, r=8, p=1, maxmem=256 * 1024**2
    )
    return f"{salt.hex()}${value.hex()}"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(bearer):
    return hmac.new(bearer.encode(), b"dean-csrf-v1", "sha256").hexdigest()


def valid_csrf(bearer, submitted):
    return bool(TOKEN.fullmatch(bearer) and len(submitted) == 64 and
                submitted.isascii() and hmac.compare_digest(csrf_token(bearer), submitted))


def verify_password(db, username, password, address):
    username = username.strip().lower()
    if not re.fullmatch(r"[a-z0-9._-]{1,64}", username) or not 1 <= len(password) <= 256:
        raise DomainError(422, "Проверь логин и пароль.")
    now = real_now()
    with write(db):
        db.execute("DELETE FROM login_attempt WHERE attempted_at < ?",
                   (timestamp(now - timedelta(minutes=5)),))
        recent, for_user = db.execute("""
            SELECT COUNT(*), COUNT(CASE WHEN username = ? THEN 1 END)
            FROM login_attempt WHERE address = ?
        """, (username, address)).fetchone()
        if recent >= 24 or for_user >= 6:
            raise DomainError(429, "Слишком много попыток входа. Попробуй через пять минут.")
        db.execute("INSERT INTO login_attempt VALUES (?, ?, ?)", (address, username, timestamp(now)))

    user = db.execute("SELECT * FROM account WHERE username = ?", (username,)).fetchone()
    stored = user["password_hash"] if user else DUMMY_HASH
    salt = bytes.fromhex(stored.split("$", 1)[0])
    if not hmac.compare_digest(hash_password(password, salt), stored) or user is None:
        raise DomainError(422, "Проверь логин и пароль.")

    return user


def personal(db, identity):
    return db.execute("SELECT 1 FROM personal_login WHERE account_id=?", (identity,)).fetchone() is not None


def find_device(db, bearer):
    if not TOKEN.fullmatch(bearer):
        return None
    secret = digest(bearer)
    return db.execute("""
        SELECT * FROM trusted_device WHERE secret_hash=? OR (previous_hash=? AND previous_until>?)
    """, (secret, secret, timestamp(real_now()))).fetchone()


def active_session(db, bearer, now):
    if not TOKEN.fullmatch(bearer):
        return None
    return db.execute("""
        SELECT * FROM session WHERE token_hash=? AND expires_at>? AND seen_at>?
    """, (digest(bearer), timestamp(now), timestamp(now - SESSION_IDLE))).fetchone()


def issue_session(db, account, now, *, method, device=None, confirmed=True):
    bearer = token()
    expires = timestamp(now + SESSION_TTL)
    if device:
        expires = min(expires, device["expires_at"])
    db.execute("""
        INSERT INTO session(token_hash,account_id,expires_at,seen_at,confirmed_at,method,device_id)
        VALUES (?,?,?,?,?,?,?)
    """, (digest(bearer), account["id"], expires, timestamp(now), timestamp(now) if confirmed else None,
          method, device["id"] if device else None))
    db.execute("INSERT INTO session_handle VALUES (?,?)", (identifier(), digest(bearer)))
    return bearer


def close_device(db, browser_token, remember_token):
    device = find_device(db, remember_token)
    session = db.execute("SELECT device_id FROM session WHERE token_hash=?", (digest(browser_token),)).fetchone()
    for device_id in {device["id"] if device else None, session["device_id"] if session else None} - {None}:
        db.execute("DELETE FROM trusted_device WHERE id=?", (device_id,))
    db.execute("DELETE FROM session WHERE token_hash=?", (digest(browser_token),))


def sign_in(db, account, old_token, old_remember, *, remember=False, method="password"):
    """Issue a new session inside the transaction that confirmed the account."""
    now = real_now()
    close_device(db, old_token, old_remember)
    db.execute("DELETE FROM session WHERE expires_at<=?", (timestamp(now),))
    db.execute("DELETE FROM trusted_device WHERE expires_at<=?", (timestamp(now),))
    if remember and account["role"] == "student" and personal(db, account["id"]):
        device_id, secret, expires = identifier(), token(), timestamp(now + DEVICE_TTL)
        db.execute("""
            INSERT INTO trusted_device(id,account_id,secret_hash,created_at,expires_at,method) VALUES (?,?,?,?,?,?)
        """, (device_id, account["id"], digest(secret), timestamp(now), expires, method))
        device = {"id": device_id, "expires_at": expires}
        return SignIn(issue_session(db, account, now, method=method, device=device), account["role"], secret, expires)
    return SignIn(issue_session(db, account, now, method=method), account["role"])


def begin_session(db, username, password, address, old_token, old_remember="", *, remember=False, browser=""):
    account = verify_password(db, username, password, address)
    with write(db):
        stored = db.execute("SELECT password_hash FROM account WHERE id=?", (account["id"],)).fetchone()
        if not stored or stored[0] != account["password_hash"]:
            raise DomainError(422, "Пароль изменился. Войди ещё раз.")
        db.execute("DELETE FROM login_attempt WHERE address=? AND username=?", (address, account["username"].lower()))
        db.execute("UPDATE personal_login SET password_used=1 WHERE account_id=?", (account["id"],))
        if TOKEN.fullmatch(browser):
            db.execute("DELETE FROM identity_attempt WHERE browser_hash=? AND intent='login'", (digest(browser),))
        return sign_in(db, account, old_token, old_remember, remember=remember)


def resume_device(db, remember):
    found = find_device(db, remember)
    if not found:
        return None
    with write(db):
        now = real_now()
        device = find_device(db, remember)
        if not device or device["expires_at"] <= timestamp(now):
            return None
        rotate = hmac.compare_digest(device["secret_hash"], digest(remember))
        if not rotate and device["previous_until"] <= timestamp(now):
            return None
        account = db.execute("SELECT id,role FROM account WHERE id=? AND role='student'", (device["account_id"],)).fetchone()
        if not account:
            return None
        secret = token() if rotate else None
        if rotate:
            db.execute("""
                UPDATE trusted_device SET previous_hash=secret_hash, previous_until=?, secret_hash=? WHERE id=?
            """, (timestamp(now + ROTATION_GRACE), digest(secret), device["id"]))
        bearer = issue_session(db, account, now, method=device["method"], device=device, confirmed=False)
        return SignIn(bearer, account["role"], secret, device["expires_at"])


def current_user(db, bearer):
    if not TOKEN.fullmatch(bearer):
        return None
    now = real_now()
    row = db.execute("""
        SELECT a.id, a.role, a.username, a.university_id, a.group_id, a.period_id, a.full_name,
               u.name AS university_name, u.timezone, s.seen_at, s.confirmed_at, s.device_id
        FROM session s JOIN account a ON a.id = s.account_id
        JOIN university u ON u.id = a.university_id
        WHERE s.token_hash=? AND s.expires_at>? AND s.seen_at>?
    """, (digest(bearer), timestamp(now), timestamp(now - SESSION_IDLE))).fetchone()
    if row and row["seen_at"] <= timestamp(now - timedelta(minutes=1)):
        db.execute("UPDATE session SET seen_at=? WHERE token_hash=? AND seen_at=?",
                   (timestamp(now), digest(bearer), row["seen_at"]))
    return row


def end_session(db, bearer, remember=""):
    with write(db):
        close_device(db, bearer, remember)


def recent_confirmation(user, now=None):
    return bool(user["confirmed_at"] and user["confirmed_at"] > timestamp((now or real_now()) - CONFIRM_TTL))


def require_session(db, bearer, account, *, confirmed=False):
    session = active_session(db, bearer, real_now())
    if not session or session["account_id"] != account:
        raise DomainError(403, "Исходный сеанс завершён. Войди в свой кабинет и начни заново.")
    if confirmed and not recent_confirmation(session):
        raise DomainError(403, "Сначала подтверди вход в разделе «Способы входа».")
    return session
