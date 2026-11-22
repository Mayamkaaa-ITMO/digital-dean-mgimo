"""Operator-only provisioning. Passwords and invitations go to new private files."""
import json
import os
from contextlib import closing

from . import access_state, auth
from .access_config import AccessConfig
from .config import WebConfig
from .db import connect, schema_version
from .domain import DomainError


def run(db_path, command, username, destination):
    if not db_path.is_file() or not username or destination is None:
        raise DomainError(422, "Укажи существующую базу, --login и новый файл --to.")
    with closing(connect(db_path, create=False)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != schema_version():
            raise DomainError(422, "Сначала выполни migrate.")
        origin, account = None, None
        if command == "max-invite":
            origin = WebConfig.from_env().public_origin
            if not origin or not AccessConfig.from_env().enabled("max", origin):
                raise DomainError(422, "Укажи публичный HTTPS-адрес, имя и токен бота MAX.")
            account = db.execute("SELECT id FROM account WHERE username=?", (username,)).fetchone()
            if not account or not auth.personal(db, account["id"]):
                raise DomainError(422, "Для приглашения нужен индивидуальный демо-кабинет.")
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                if command == "demo-access":
                    password = auth.token()
                    json.dump({username: password}, output, indent=2)
                    output.write("\n")
                    output.flush()
                    os.fsync(output.fileno())
                    access_state.new_demo_account(db, username, password)
                else:
                    secret = access_state.invite(db, account["id"])
                    output.write(f"{origin}/auth/max/invite#{secret}\n")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
