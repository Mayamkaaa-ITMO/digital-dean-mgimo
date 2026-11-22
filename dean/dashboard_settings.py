import json

from .db import write
from .domain import DomainError, role
from .learning import optional_int

PANELS={
    "demo":{"exam":"Ближайший экзамен","tasks":"Задачи на сегодня",
            "overdue":"Просроченные задачи","documents":"Готовые документы"},
    "public":{"tasks":"Личные задачи"},
}
SHORTCUTS={
    "grades":("/grades","Успеваемость"),"requests":("/requests?mode=catalog","Заявки"),
    "certificate":("/services/enrolment-certificate","Заказать справку"),
    "task":("/tasks/new","Создать задачу"),"event":("/events/new","Добавить событие"),
    "subjects":("/study/subjects","Мои предметы"),"preparation":("/study/preparation","Подготовка"),
    "calendar":("/schedule","Расписание"),
}
DEFAULT_ACTIONS={"demo":("grades","requests"),"public":("grades","requests")}


def checked_mode(value):
    if value not in PANELS:
        raise DomainError(422,"Выбери демонстрационную или публичную главную.")
    return value


def preferences(db,user,mode):
    row=db.execute("SELECT * FROM dashboard_preference WHERE owner_id=? AND mode=?",(user["id"],mode)).fetchone()
    return ([key for key in json.loads(row["panels_json"]) if key in PANELS[mode]],
            json.loads(row["shortcuts_json"]),row["version"]) if row else (
        list(PANELS[mode]),list(DEFAULT_ACTIONS[mode]),0)


def order(fields,prefix,choices):
    selected=[]
    for key,value in choices.items():
        label=value if isinstance(value,str) else value[1]
        rank=optional_int(fields.get(prefix+key),label,len(choices))
        if rank:
            selected.append((rank,key))
    if len({rank for rank,_ in selected})!=len(selected):
        raise DomainError(422,"Используй разные номера для видимых элементов.")
    return [key for _,key in sorted(selected)]


def save(db,user,mode,fields):
    role(user,"student")
    mode=checked_mode(mode)
    if fields.get("reset")=="1":
        panels,actions=list(PANELS[mode]),list(DEFAULT_ACTIONS[mode])
    else:
        panels,actions=order(fields,"panel_",PANELS[mode]),order(fields,"shortcut_",SHORTCUTS)
    with write(db):
        _,_,version=preferences(db,user,mode)
        if str(version)!=fields.get("version"):
            raise DomainError(409,"Настройки уже изменились. Открой форму заново.")
        db.execute("""
            INSERT INTO dashboard_preference VALUES (?,?,?,?,?)
            ON CONFLICT(owner_id,mode) DO UPDATE SET panels_json=excluded.panels_json,
                shortcuts_json=excluded.shortcuts_json,version=excluded.version
        """,(user["id"],mode,json.dumps(panels),json.dumps(actions),version+1))


