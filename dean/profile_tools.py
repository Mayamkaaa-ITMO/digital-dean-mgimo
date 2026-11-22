"""Owner-scoped account settings and internal support."""
from . import applications, auth, portfolio, public_schedule, services
from .db import write
from .domain import DomainError, field, identifier, require, role, timestamp

SENSITIVE = frozenset({"grade", "grade_changed", "finance", "budget", "award", "charge", "academic", "result", "disbursement"})
SUPPORT_STATES = {"open":"Открыто", "review":"В работе", "closed":"Закрыто"}
SUPPORT_TOPICS = {"": "Общее", "calendar": "Расписание", "study": "Подготовка", "import": "Импорт и экспорт"}


def sessions(db, user, session):
    now = auth.real_now()
    return list(db.execute("""
        SELECT h.id,s.expires_at,s.token_hash=? AS current FROM session s JOIN session_handle h USING(token_hash)
        WHERE s.account_id=? AND s.expires_at>? AND s.seen_at>? ORDER BY current DESC,s.expires_at DESC,h.id
    """, (auth.digest(session), user["id"], timestamp(now), timestamp(now - auth.SESSION_IDLE))))


def devices(db, user):
    return list(db.execute("""
        SELECT id,expires_at FROM trusted_device WHERE account_id=? AND expires_at>?
        ORDER BY created_at DESC
    """, (user["id"], timestamp(auth.real_now()))))


def revoke_device(db, user, current, identity):
    with write(db):
        session = auth.require_session(db, current, user["id"])
        if not db.execute("DELETE FROM trusted_device WHERE id=? AND account_id=?",
                          (identity, user["id"])).rowcount:
            raise DomainError(404, "Устройство недоступно.")
        return session["device_id"] == identity


def revoke_sessions(db, user, current, handle=None):
    token = auth.digest(current)
    with write(db):
        session = auth.require_session(db, current, user["id"])
        if handle:
            row = require(db.execute("""
                SELECT s.token_hash,s.device_id FROM session s JOIN session_handle h USING(token_hash)
                WHERE h.id=? AND s.account_id=?
            """, (handle,user["id"])).fetchone())
            if row["token_hash"]==token:
                raise DomainError(422,"Для текущего сеанса используй кнопку «Выйти».")
            if row["device_id"] and row["device_id"] != session["device_id"]:
                db.execute("DELETE FROM trusted_device WHERE id=? AND account_id=?", (row["device_id"], user["id"]))
            db.execute("DELETE FROM session WHERE token_hash=? AND account_id=?", (row["token_hash"],user["id"]))
        else:
            db.execute("DELETE FROM trusted_device WHERE account_id=? AND id IS NOT ?",
                       (user["id"], session["device_id"]))
            db.execute("DELETE FROM session WHERE account_id=? AND token_hash<>?", (user["id"],token))


def hidden_preview(db,user):
    row=db.execute("SELECT hide_preview FROM profile_privacy WHERE owner_id=?",(user["id"],)).fetchone()
    return bool(row and row[0])


def set_preview_in_transaction(db,user,enabled):
    db.execute("""
        INSERT INTO profile_privacy VALUES (?,?)
        ON CONFLICT(owner_id) DO UPDATE SET hide_preview=excluded.hide_preview
    """,(user["id"],int(enabled)))


def set_preview(db,user,enabled):
    role(user,"student")
    with write(db):
        set_preview_in_transaction(db,user,enabled)


def service_options(db,user,selection):
    if selection:
        source=public_schedule.SOURCES[selection[0]]
        return [(item["id"],item["label"],item["url"]) for item in source["services"]]
    return [(row["id"],row["name"],"/services/"+row["id"]) for row in applications.catalog(db,user)]


def links(db,user):
    return list(db.execute("SELECT * FROM personal_link WHERE owner_id=? ORDER BY kind,title,id",(user["id"],)))


def add_link(db,user,selection,kind,service,title,url,key):
    role(user,"student")
    if kind=="service":
        selected=next(((label,href) for ref,label,href in service_options(db,user,selection) if ref==service),None)
        if selected is None:
            raise DomainError(422,"Выбери сервис из текущего каталога.")
        title,url=selected
    elif kind=="personal":
        title=field(title,"Название",100)
        url=portfolio.valid_url(url)
        if not url:
            raise DomainError(422,"Укажи HTTPS-ссылку.")
    else:
        raise DomainError(422,"Выбери тип ссылки.")

    def apply():
        if selection != public_schedule.context(db,user):
            raise DomainError(409,"Контекст изменился. Открой каталог заново.")
        found=db.execute("SELECT id FROM personal_link WHERE owner_id=? AND url=?",(user["id"],url)).fetchone()
        if found:
            return found["id"]
        if len(links(db,user))>=40:
            raise DomainError(409,"Убери ненужную запись из избранного перед добавлением.")
        lid=identifier()
        db.execute("INSERT INTO personal_link VALUES (?,?,?,?,?)",(lid,user["id"],title,url,kind))
        return lid
    return services.command(db,user,"profile.link",key,[kind,title,url],apply)


def remove_link(db,user,lid):
    role(user,"student")
    with write(db):
        if not db.execute("DELETE FROM personal_link WHERE owner_id=? AND id=?",(user["id"],lid)).rowcount:
            raise DomainError(404,"Ссылка недоступна.")


def tickets(db,user,tid=None):
    query="""SELECT t.*, author.full_name AS reply_author FROM support_ticket t JOIN account a ON a.id=t.owner_id LEFT JOIN account author ON author.id=t.replied_by WHERE a.university_id=?"""
    params=(user["university_id"],)
    if user["role"]=="staff":
        query+=" AND EXISTS (SELECT 1 FROM staff_group g WHERE g.university_id=a.university_id AND g.group_id=a.group_id AND g.staff_id=?)"
    else:
        role(user,"student")
        query+=" AND a.id=?"
    params+=(user["id"],)
    if tid:
        return require(db.execute(query+" AND t.id=?",(*params,tid)).fetchone())
    return list(db.execute(query+" ORDER BY t.state='closed', t.created_at DESC,t.id LIMIT 50",params))


def ticket_create(db,user,selection,inputs,key):
    role(user,"student")
    title, body=field(inputs.get("title",""),"Тема",120),field(inputs.get("body",""),"Описание",1800)
    topic=inputs.get("topic","")
    if topic not in SUPPORT_TOPICS:
        raise DomainError(422,"Выбери раздел.")
    use_context=inputs.get("context")=="1"
    context=SUPPORT_TOPICS[topic]
    if use_context:
        context+=" · "+(" · ".join(selection) if selection else "Учебное демо")

    def apply():
        tid=identifier()
        db.execute("""
            INSERT INTO support_ticket(id,owner_id,title,body,context,created_at) VALUES (?,?,?,?,?,?)
        """, (tid,user["id"],title,body,context,timestamp()))
        return tid
    return services.command(db,user,"profile.support",key,[title,body,context],apply)


def ticket_update(db,user,tid,state,version,key,reply=""):
    reply = field(reply, "Ответ", 1800) if state == "closed" else ""
    role(user,"staff")

    def apply():
        row=tickets(db,user,tid)
        if str(row["version"])!=version or (row["state"],state) not in {("open","review"),("review","closed")}:
            raise DomainError(409,"Обращение изменилось или переход недоступен.")
        stamp = timestamp()
        db.execute("UPDATE support_ticket SET state=?, reply=?, replied_by=?, replied_at=?, version=version+1 WHERE id=?",
                   (state, reply, user["id"] if reply else None, stamp if reply else None, tid))
        if reply:
            services.notify(db, user["university_id"], row["owner_id"], "support", tid, "support-reply:"+tid,
                            "Ответ поддержки", row["title"], stamp)
        return tid
    return services.command(db,user,"profile.support.state",key,[tid,state,version,reply] if reply else [tid,state,version],apply)
