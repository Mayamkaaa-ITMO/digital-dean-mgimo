"""Account-scoped assistant transcripts, independent of the model provider."""
import json

from .db import write
from .domain import DomainError, identifier, require, timestamp


_RECENT = """
    SELECT c.id, c.title, c.updated_at FROM assistant_conversation AS c
    WHERE c.owner_id=?
    ORDER BY c.updated_at DESC, (SELECT MAX(m.rowid)
                                 FROM assistant_message AS m WHERE m.conversation_id=c.id) DESC
"""


def conversations(db, user):
    return db.execute(_RECENT, (user["id"],)).fetchall()


def current(db, user, selected=""):
    if selected == "new":
        return None
    if selected:
        return require(db.execute(
            "SELECT id, title, updated_at FROM assistant_conversation WHERE owner_id=? AND id=?",
            (user["id"], selected),
        ).fetchone())
    return db.execute(_RECENT + " LIMIT 1", (user["id"],)).fetchone()


_MESSAGE = """
    SELECT m.*, c.title, p.expires_at, p.result_path
    FROM assistant_message AS m
    JOIN assistant_conversation AS c ON c.id=m.conversation_id AND c.owner_id=m.owner_id
    LEFT JOIN assistant_proposal AS p ON p.owner_id=m.owner_id AND p.id=json_extract(m.reply_json, '$.proposal')
"""


def messages(db, user, conversation):
    if not conversation:
        return []
    return db.execute(
        _MESSAGE + " WHERE m.owner_id=? AND m.conversation_id=? ORDER BY m.created_at, m.rowid",
        (user["id"], conversation["id"]),
    ).fetchall()


def previous_question(db, user, conversation, fallback=""):
    if not conversation:
        return fallback
    row = db.execute(
        "SELECT question FROM assistant_message WHERE owner_id=? AND conversation_id=? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (user["id"], conversation["id"]),
    ).fetchone()
    return row["question"] if row else fallback


def response(db, user, key, question, selected):
    row = db.execute(
        _MESSAGE + " WHERE m.owner_id=? AND m.command_id=?", (user["id"], key),
    ).fetchone()
    if row and (row["question"] != question or selected not in {"", "new", row["conversation_id"]}):
        raise DomainError(409, "Этот запрос уже отправлен. Отправь новое сообщение.")
    return row


def record(db, user, selected, question, key, reply):
    owner = user["id"]
    with write(db):
        existing = response(db, user, key, question, selected)
        if existing:
            return existing
        conversation = current(db, user, selected)
        when = timestamp()
        if conversation:
            conversation_id = conversation["id"]
            db.execute("UPDATE assistant_conversation SET updated_at=? WHERE id=? AND owner_id=?",
                       (when, conversation_id, owner))
        else:
            conversation_id = identifier()
            words = " ".join(question.split())
            title = words if len(words) <= 68 else words[:67].rstrip() + "…"
            db.execute("INSERT INTO assistant_conversation VALUES (?, ?, ?, ?)",
                       (conversation_id, owner, title, when))
        db.execute("INSERT INTO assistant_message VALUES (?, ?, ?, ?, ?, ?)",
                   (owner, conversation_id, key, question, json.dumps(reply, ensure_ascii=False), when))
        return response(db, user, key, question, conversation_id)
