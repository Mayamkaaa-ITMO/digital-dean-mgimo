"""Pairing and read-only Telegram commands over the dean's existing records."""
from collections import Counter
from datetime import timedelta
import hmac
import json
import re
import secrets
from zoneinfo import ZoneInfo

from . import applications, assistant, auth, calendar_import, calendar_tools, personal_data, public_schedule, services, workspace
from .clock import now
from .db import write
from .domain import DomainError, identifier, role, timestamp
from .schedule import local_value

CATEGORIES={"schedule":"Переносы занятий","task":"Сроки задач","application":"Статусы заявлений",
            "work_result":"Результаты учебных работ"}
PROOF=re.compile(r"[0-9a-f]{12}")
PAIR_TTL=timedelta(minutes=10)
HELP="/today · сегодня\n/week · неделя\n/tasks · задачи\n/requests · статусы заявок\n/settings · настройки\n/cabinet · кабинет\n/stop · отключить"
STAFF_HELP="/today · сегодня\n/week · неделя\n/requests · очередь заявок\n/settings · привязка\n/cabinet · кабинет\n/stop · отключить"
MENUS={
    "guest":(("🔗 Подключить кабинет","❔ Помощь"),),
    "student":(("📅 Сегодня","🗓 Неделя"),("✅ Задачи","📄 Заявки"),
               ("🏠 Кабинет","⚙ Настройки")),
    "staff":(("📅 Сегодня","🗓 Неделя"),("📄 Очередь заявок","🏠 Кабинет"),
             ("⚙ Настройки","❔ Помощь")),
}
ALIASES={
    "сегодня":"/today","неделя":"/week","задачи":"/tasks","заявки":"/requests",
    "очередь заявок":"/requests","настройки":"/settings","помощь":"/help","подключить кабинет":"/connect",
    "кабинет":"/cabinet",
}
for rows in MENUS.values():
    for row in rows:
        for label in row:
            ALIASES[label.casefold()]=ALIASES[label.split(" ",1)[1].casefold()]


def keyboard(menu):
    if menu == "remove":
        return {"remove_keyboard":True}
    if menu in MENUS:
        return {"keyboard":[[{"text":text} for text in row] for row in MENUS[menu]],
                "resize_keyboard":True,"is_persistent":True}
    return None


def active_binding(db,owner):
    return db.execute("SELECT * FROM telegram_binding WHERE owner_id=? AND revoked_at IS NULL",(owner,)).fetchone()


def pending_pair(db,user,bot):
    return db.execute("SELECT * FROM telegram_pair WHERE owner_id=? AND bot=? AND expires_at>?",
                      (user["id"],bot,timestamp())).fetchone()


def enqueue(db,bot,chat,kind,key,body,*,binding=None,pair=None,notification=None,menu=""):
    stamp=timestamp()
    db.execute("""
        INSERT INTO telegram_outbox(id,bot,chat_id,kind,key,body,binding_id,pair_id,notification_id,due_at,created_at,menu)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(bot,key) DO NOTHING
    """,(identifier(),bot,chat,kind,key,body[:3500],binding,pair,notification,stamp,stamp,menu))


def cancel_pair(db,owner):
    db.execute("""
        UPDATE telegram_outbox SET state='cancelled',body='',chat_id=0,lease_id=NULL,lease_until=NULL
        WHERE pair_id IN (SELECT id FROM telegram_pair WHERE owner_id=?) AND state IN ('pending','sending')
    """,(owner,))
    db.execute("DELETE FROM telegram_pair WHERE owner_id=?",(owner,))


def begin(db,user,config):
    if not config.name:
        raise DomainError(409,"Канал Telegram ещё не настроен.")
    nonce=auth.token()
    with write(db):
        if active_binding(db,user["id"]):
            raise DomainError(409,"Сначала отключи текущую привязку.")
        previous=pending_pair(db,user,config.name)
        if previous and previous["created_at"]>timestamp(now()-timedelta(seconds=15)):
            raise DomainError(429,"Повтори выдачу ссылки через несколько секунд.")
        cancel_pair(db,user["id"])
        db.execute("""
            INSERT INTO telegram_pair(id,owner_id,bot,nonce_hash,expires_at,created_at) VALUES (?,?,?,?,?,?)
        """,(identifier(),user["id"],config.name,auth.digest(nonce),timestamp(now()+PAIR_TTL),timestamp()))
    return nonce


def claim_pair(db,config,chat,nonce):
    if not auth.TOKEN.fullmatch(nonce):
        return False
    row=db.execute("""
        SELECT * FROM telegram_pair WHERE nonce_hash=? AND bot=? AND expires_at>? AND attempts<6
    """,(auth.digest(nonce),config.name,timestamp())).fetchone()
    if not row or active_binding(db,row["owner_id"]):
        return False
    if not personal_data.permits(db, {"id": row["owner_id"]}, personal_data.Settings.from_env()):
        return False
    if row["chat_id"] is not None:
        return row["chat_id"] == chat
    if db.execute("SELECT 1 FROM telegram_binding WHERE bot=? AND chat_id=? AND revoked_at IS NULL",
                  (config.name,chat)).fetchone():
        return False
    proof=secrets.token_hex(6)
    db.execute("UPDATE telegram_pair SET chat_id=?,proof_hash=? WHERE id=?",(chat,auth.digest(proof),row["id"]))
    enqueue(db,config.name,chat,"proof","proof:"+row["id"],
            "Код привязки: "+proof+"\nВернись в «Профиль → Telegram» и введи этот код в кабинете.",pair=row["id"])
    return True


def confirm(db,user,config,code):
    code=code.strip().lower()
    if not PROOF.fullmatch(code):
        raise DomainError(422,"Укажи 12 символов из сообщения бота.")
    accepted=False
    with write(db):
        row=pending_pair(db,user,config.name)
        if not row or row["chat_id"] is None or row["attempts"]>=6:
            raise DomainError(409,"Нет ожидающей привязки. Начни заново.")
        if not config.permits(row["chat_id"]):
            raise DomainError(403,"Этот чат не входит в настроенный тестовый список.")
        if not hmac.compare_digest(row["proof_hash"],auth.digest(code)):
            db.execute("UPDATE telegram_pair SET attempts=attempts+1 WHERE id=?",(row["id"],))
        else:
            if active_binding(db,user["id"]) or db.execute("""
                    SELECT 1 FROM telegram_binding WHERE bot=? AND chat_id=? AND revoked_at IS NULL
                """,(config.name,row["chat_id"])).fetchone():
                raise DomainError(409,"Аккаунт или чат уже привязан.")
            binding = identifier()
            db.execute("""
                INSERT INTO telegram_binding(id,owner_id,bot,chat_id,linked_at,timezone) VALUES (?,?,?,?,?,?)
            """,(binding,user["id"],config.name,row["chat_id"],timestamp(),user["timezone"]))
            cancel_pair(db,user["id"])
            enqueue(db,config.name,row["chat_id"],"command","welcome:"+binding,
                    "Кабинет подключён. Выбери раздел под полем сообщения.",
                    binding=binding,menu=user["role"])
            accepted=True
    if not accepted:
        raise DomainError(403,"Код не совпал с сообщением бота.")


def revoke_in_transaction(db,row):
    stamp=timestamp()
    db.execute("UPDATE telegram_binding SET revoked_at=?,enabled=0,chat_id=0,version=version+1 WHERE id=?",(stamp,row["id"]))
    db.execute("""
        UPDATE telegram_outbox SET state='cancelled',body='',chat_id=0,lease_id=NULL,lease_until=NULL
        WHERE binding_id=? AND state IN ('pending','sending')
    """,(row["id"],))
    cancel_pair(db,row["owner_id"])


def revoke(db,user):
    with write(db):
        row=active_binding(db,user["id"])
        if row:
            revoke_in_transaction(db,row)
        else:
            cancel_pair(db,user["id"])


def settings(db,user,fields):
    role(user,"student")
    zone=calendar_import.timezone(fields.get("timezone"))
    start=calendar_tools.minute_value(fields.get("quiet_start")).strftime("%H:%M")
    end=calendar_tools.minute_value(fields.get("quiet_end")).strftime("%H:%M")
    selected=[kind for kind in CATEGORIES if fields.get(kind)=="1"]
    with write(db):
        row=active_binding(db,user["id"])
        if not row or str(row["version"])!=fields.get("version"):
            raise DomainError(409,"Привязка или настройки изменились.")
        db.execute("""
            UPDATE telegram_binding SET enabled=?,categories_json=?,timezone=?,quiet_start=?,quiet_end=?,summary=?,
                version=version+1 WHERE id=?
        """,(int(fields.get("enabled")=="1"),json.dumps(selected),zone,start,end,int(fields.get("summary")=="1"),row["id"]))


def linked_user(db,binding):
    user = db.execute("""
        SELECT a.*,u.timezone FROM account a JOIN university u ON u.id=a.university_id
        WHERE a.id=? AND a.role IN ('student','staff')
    """,(binding["owner_id"],)).fetchone()
    return user if user and personal_data.permits(db, user, personal_data.Settings.from_env()) else None


def tagline(config,path):
    return "\nКабинет: "+config.site+path if config.site else "\nСсылки появятся после настройки внешнего адреса."


def schedule_text(db,user,week=False):
    selection=public_schedule.context(db,user)
    scope=calendar_tools.period_for(user,selection)
    today=now().astimezone(ZoneInfo(scope.timezone)).date()
    first=today-timedelta(days=today.weekday()) if week else today
    stop=first+timedelta(days=7 if week else 1)
    values=calendar_tools.calendar(db,user,selection,first,stop)
    lessons=[event for event in values if event.kind in {"lesson","exam","event"}]
    if selection:
        source=public_schedule.SOURCES[selection[0]]
        group=source["groups"][selection[1]]
        pub=public_schedule.public_week(db,selection,first)
        context=f'{public_schedule.source_label(source)} · {group["label"]} · {scope.timezone}'
        dates=[first+timedelta(days=n) for n in range((stop-first).days)]
        freshness=f'Данные источника прочитаны {pub["observed_at"]}.' if pub else "Данных вуза на эту неделю пока нет."
        if pub and not all(public_schedule.covered_day(selection,day,pub) for day in dates):
            freshness+=" Охват части периода не подтверждён."
    else:
        context=f'Учебное демо · {services.profile(db,user)["group_name"]} · {scope.timezone}'
        freshness="Демонстрационный календарь."
    lines = []
    day = None
    for event in lessons[:20]:
        start = local_value(event.start, scope.timezone)
        if start.date() != day:
            day = start.date()
            lines.append(f"\n{start:%d.%m}")
        stop = local_value(event.end, scope.timezone) if event.end else None
        hours = f"{start:%H:%M}" + (f"–{stop:%H:%M}" if stop and stop.date() == day else "")
        lines.append(f"• {hours} · {event.title}" + (f" · {event.room}" if event.room else "")
                     + (f" · {event.subtitle}" if event.kind == "event" else ""))
    if len(lessons)>20:
        lines.append(f'И ещё {len(lessons)-20} в кабинете.')
    return "\n".join([context,freshness,*(lines or ["Занятий в известных данных нет."])])


def response(db,user,config,command):
    staff=user["role"]=="staff"
    if command in {"/today","/week"}:
        if staff:
            answer=assistant.schedule_answer(db,user,{"period":"week" if command=="/week" else "today"})
            return "Занятия твоих групп\n"+answer["text"]+tagline(config,answer["link"])
        heading="🗓 Расписание на неделю" if command=="/week" else "📅 Расписание на сегодня"
        return heading+"\n\n"+schedule_text(db,user,command=="/week")+tagline(config,"/schedule")
    if command=="/tasks" and not staff:
        rows=workspace.tasks(db,user,state="todo")
        items=[f'• {row["title"]}'+(f' · до {local_value(row["due_at"],user["timezone"]):%d.%m %H:%M}'
                                 if row["due_at"] else "") for row in rows[:8]]
        return "✅ Задачи в работе\n\n"+"\n".join(items or ["Незавершённых задач нет."])+tagline(config,"/tasks")
    if command=="/requests":
        counts=Counter(row["status"] for row in applications.applications(db,user))
        lines=[f'{applications.STATUSES.get(state,state)}: {count}' for state,count in counts.items()]
        heading="📄 Очередь заявок" if staff else "📄 Мои заявки"
        return heading+"\n\n"+"\n".join(lines or ["Заявлений нет."])+tagline(config,"/staff" if staff else "/requests")
    if command=="/cabinet":
        return "🏠 Открыть Цифровой деканат"+tagline(config,"/staff" if staff else "/")
    if command=="/settings":
        text="Управляй привязкой в кабинете." if staff else "Категории, уведомления и тихие часы настраиваются в кабинете."
        return text+tagline(config,"/profile/telegram")
    if command in {"/start","/help"}:
        return "Привет, я Вишка. Выбери раздел под полем сообщения.\n\n"+(STAFF_HELP if staff else HELP)
    return "Выбери действие под полем сообщения или введи /help."


def private_message(update,config):
    message=update.get("message")
    if not isinstance(message,dict):
        return None
    chat,person,text=message.get("chat"),message.get("from"),message.get("text")
    if not isinstance(chat,dict) or not isinstance(person,dict) or not isinstance(text,str):
        return None
    dest=chat.get("id")
    if (chat.get("type")!="private" or not config.permits(dest)
            or type(person.get("id")) is not int or person["id"]!=dest or person.get("is_bot") is True):
        return None
    if not 0<len(text)<=256 or not (text.startswith("/") or text.strip().casefold() in ALIASES):
        return None
    return dest,text


def handle_update(db,config,update_id,update):
    request=private_message(update,config)
    if not request:
        return
    chat, text=request
    text=ALIASES.get(text.strip().casefold(),text)
    head,_,argument=text.strip().partition(" ")
    command,at,recipient=head.partition("@")
    if at and recipient.casefold()!=config.name:
        return
    binding=db.execute("""
        SELECT * FROM telegram_binding WHERE bot=? AND chat_id=? AND revoked_at IS NULL
    """,(config.name,chat)).fetchone()
    recent=db.execute("""
        SELECT count(*) FROM telegram_update WHERE bot=? AND chat_key=? AND handled_at>?
    """,(config.name,auth.digest(f"{config.name}:{chat}"),timestamp(now()-timedelta(minutes=1)))).fetchone()[0]
    if recent>12:
        return
    key=f"update:{update_id}"
    user=linked_user(db,binding) if binding else None
    if command=="/start" and argument:
        if user:
            enqueue(db,config.name,chat,"command",key,"Кабинет уже подключён. Выбери раздел под полем сообщения.",
                    binding=binding["id"],menu=user["role"])
        elif not claim_pair(db,config,chat,argument.strip()):
            enqueue(db,config.name,chat,"generic",key,"Ссылка недействительна. Начни привязку в кабинете."
                    +tagline(config,"/profile/telegram"),menu="guest")
        return
    if command=="/stop" and binding:
        revoke_in_transaction(db,binding)
        enqueue(db,config.name,chat,"generic",key,"Привязка отключена. Напиши /start для новой привязки.",menu="remove")
        return
    if user:
        enqueue(db,config.name,chat,"command",key,response(db,user,config,command),binding=binding["id"],
                menu=user["role"])
    elif binding:
        enqueue(db,config.name,chat,"generic",key,"Чтобы снова получать данные, открой кабинет и проверь согласие."
                +tagline(config,"/privacy/consent"),menu="remove")
    else:
        enqueue(db,config.name,chat,"generic",key,"Привет, я Вишка 👋\n\n"
                "1. Открой настройки Telegram в своём кабинете.\n"
                "2. Нажми «Подключить Telegram» и открой личную ссылку.\n"
                "3. Вернись в кабинет и введи код из личного чата."+tagline(config,"/profile/telegram"),menu="guest")
