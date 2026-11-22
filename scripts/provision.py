"""Operator commands for an empty deployment and individually issued accounts."""
import argparse
from contextlib import closing
from datetime import date, datetime
from getpass import getpass
import json
from pathlib import Path
import re
import sqlite3
import sys
from zoneinfo import ZoneInfo

from dean import auth, campus_map
from dean.db import connect, database_path, migrate, write
from dean.domain import identifier, timestamp
from dean.seed import UNIVERSITY as SAMPLE_TENANT


def code(value):
    if not isinstance(value,str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}",value):
        raise ValueError("Идентификатор должен содержать 2–64 латинских символа, цифры или дефисы.")
    return value


def label(value, limit=140, required=True):
    if not isinstance(value,str) or len(value.strip())>limit or required and not value.strip():
        raise ValueError("Проверь обязательные поля и длину текста.")
    return value.strip()


def date_of(value):
    return date.fromisoformat(value).isoformat()


def at(value,zone):
    value = datetime.fromisoformat(value)
    return timestamp(value if value.tzinfo else value.replace(tzinfo=zone))


def register_site(db, config):
    site=config["university"]
    ident=code(site["id"])
    if ident==SAMPLE_TENANT:
        raise ValueError("Для установки используй свой идентификатор вуза.")
    zone=ZoneInfo(site["timezone"])
    with write(db):
        # Existing SQLite schemas use this fixed legacy column value.
        db.execute("""INSERT INTO university VALUES (?,?,?,'demo_university') ON CONFLICT(id)
                      DO UPDATE SET name=excluded.name,timezone=excluded.timezone""",
                   (ident,label(site["name"]),zone.key))
        for item in config["periods"]:
            db.execute("""INSERT INTO academic_period VALUES (?,?,?,?,?) ON CONFLICT(university_id,id)
                          DO UPDATE SET label=excluded.label,starts_on=excluded.starts_on,ends_on=excluded.ends_on""",
                       (ident,code(item["id"]),label(item["label"]),date_of(item["from"]),date_of(item["to"])))
        for item in config["programs"]:
            db.execute("""INSERT INTO program VALUES (?,?,?,?) ON CONFLICT(university_id,id)
                          DO UPDATE SET faculty=excluded.faculty,name=excluded.name""",
                       (ident,code(item["id"]),label(item["faculty"]),label(item["name"])))
        for item in config["groups"]:
            year=item["year"]
            if type(year) is not int or not 1<=year<=8:
                raise ValueError("Проверь номер курса группы.")
            db.execute("""INSERT INTO study_group VALUES (?,?,?,?,?) ON CONFLICT(university_id,id)
                          DO UPDATE SET name=excluded.name,program_id=excluded.program_id,course_year=excluded.course_year""",
                       (ident,code(item["id"]),label(item["name"]),code(item["program"]),year))
        for item in config.get("subjects",[]):
            db.execute("""INSERT INTO subject VALUES (?,?,?) ON CONFLICT(university_id,id)
                          DO UPDATE SET name=excluded.name""",
                       (ident,code(item["id"]),label(item["name"])))
        for item in config.get("services",[]):
            db.execute("""INSERT INTO service(university_id,id,name,description,placeholder) VALUES (?,?,?,?,?) ON CONFLICT(university_id,id)
                          DO UPDATE SET name=excluded.name,description=excluded.description,placeholder=excluded.placeholder""",
                       (ident,code(item["id"]),label(item["name"]),label(item["description"],700),label(item.get("hint",""),200,False)))
        for item in config.get("contacts",[]):
            place=item.get("map_place","")
            if place and place not in campus_map.campus().places:
                raise ValueError("Проверь идентификатор кабинета на карте.")
            db.execute("""INSERT INTO campus_contact(university_id,kind,name,description,office,hours,email,map_place)
                          VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(university_id,kind,name) DO UPDATE SET
                          description=excluded.description,office=excluded.office,hours=excluded.hours,
                          email=excluded.email,map_place=excluded.map_place""",
                       (ident,item["kind"],label(item["name"]),label(item["description"],300),label(item["office"]),
                        label(item.get("hours",""),140,False),label(item.get("email",""),140,False),place))
        for item in config.get("lessons",[]):
            # Initial schedules are safe to import twice. Staff moves take precedence over a re-import.
            db.execute("""INSERT INTO schedule_event(id,university_id,group_id,period_id,subject_id,starts_at,ends_at,
                          timezone,kind,room,teacher,source_id,source_event_id,origin)
                          VALUES (?,?,?,?,?,?,?,?,?,?,?,'operator',?,'user')
                          ON CONFLICT(university_id,source_id,group_id,period_id,source_event_id) DO UPDATE SET
                          subject_id=excluded.subject_id,starts_at=excluded.starts_at,ends_at=excluded.ends_at,
                          kind=excluded.kind,room=excluded.room,teacher=excluded.teacher WHERE schedule_event.version=1""",
                       (identifier(),ident,code(item["group"]),code(item["period"]),code(item["subject"]),
                        at(item["from"],zone),at(item["to"],zone),zone.key,label(item["kind"],80),
                        label(item.get("room",""),80,False),label(item.get("teacher",""),100,False),code(item["id"])))
    return ident


def register_account(db, site, username, full_name, role, password, groups, period):
    if not re.fullmatch(r"[a-z0-9._-]{2,64}",username):
        raise ValueError("Проверь логин.")
    if not 14<=len(password)<=256:
        raise ValueError("Используй пароль от 14 до 256 символов.")
    site=code(site)
    if site==SAMPLE_TENANT:
        raise ValueError("Для личных аккаунтов используй свой вуз.")
    if role=="student" and (len(groups)!=1 or not period) or role=="staff" and (not groups or period):
        raise ValueError("Студенту нужна одна группа и период. Сотруднику нужны закреплённые группы.")
    full_name=label(full_name)
    digest=auth.hash_password(password)
    uid=identifier()
    with write(db):
        group=code(groups[0]) if role=="student" else None
        db.execute("""INSERT INTO account(id,username,password_hash,full_name,role,university_id,group_id,period_id)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (uid,username,digest,full_name,role,site,group,code(period) if period else None))
        db.execute("INSERT INTO personal_login(account_id) VALUES (?)",(uid,))
        if role=="staff":
            db.executemany("INSERT INTO staff_group VALUES (?,?,?)",[(site,uid,code(g)) for g in set(groups)])
    return uid


def main():
    parser=argparse.ArgumentParser(description="Подготовка вуза и личных аккаунтов")
    modes=parser.add_subparsers(dest="mode", required=True)
    site=modes.add_parser("site",help="Добавить или обновить справочники из JSON")
    site.add_argument("path",type=Path)
    account=modes.add_parser("account",help="Выпустить личный аккаунт")
    account.add_argument("--site",required=True)
    account.add_argument("--login",required=True)
    account.add_argument("--name",required=True)
    account.add_argument("--role",choices=("student","staff"),required=True)
    account.add_argument("--group",action="append",required=True)
    account.add_argument("--period")
    args=parser.parse_args()
    path=database_path()
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    try:
        if args.mode=="account":
            if not sys.stdin.isatty():
                parser.error("Для ввода пароля запусти команду в Terminal с TTY.")
            password=getpass("Пароль для нового аккаунта: ")
            if password!=getpass("Повтори пароль: "):
                parser.error("Пароли различаются.")
        with closing(connect(path)) as db:
            migrate(db)
            if args.mode=="site":
                raw=sys.stdin.read(2_000_001) if str(args.path)=="-" else args.path.read_text()
                if len(raw)>2_000_000:
                    raise ValueError("Файл справочников слишком большой.")
                print("Настроен вуз",register_site(db,json.loads(raw)))
            else:
                register_account(db,args.site,args.login,args.name,args.role,password,args.group,args.period)
                print("Выпущен аккаунт",args.login)
    except (ValueError,KeyError,sqlite3.IntegrityError,OSError) as error:
        parser.error(str(error))


if __name__=="__main__":
    main()
