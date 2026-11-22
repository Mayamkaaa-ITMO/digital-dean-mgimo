import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from socket import AF_INET6
from socketserver import ThreadingMixIn
from time import sleep
from urllib.parse import unquote
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from .config import LOOPBACK
from .db import backup, connect, database_path, migrate, schema_version
from .domain import DomainError
from .seed import credentials_path, seed


class LocalServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class IPv6LocalServer(LocalServer):
    address_family = AF_INET6


class LocalRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        line = self.requestline
        target = unquote(self.path).split("?", 1)[0]
        if str(code)=="200" and target.startswith("/coworking/") and target.endswith(("/frame","/status")):
            return
        if target.startswith("/auth/"):
            self.requestline = f"{self.command} /auth/[redacted] {self.request_version}"
        if target == "/login":
            self.requestline = f"{self.command} /login {self.request_version}"
        for path in ("/calendar/feed/", "/shared/document/", "/calendar/shared/"):
            if path in target:
                self.requestline = f"{self.command} {path}[redacted] {self.request_version}"
                break
        try:
            super().log_request(code, size)
        finally:
            self.requestline = line


def main():
    parser = argparse.ArgumentParser(description="Демонстрационный цифровой деканат")
    parser.add_argument("command", choices=("migrate", "seed", "credentials", "backup", "reminders", "serve",
                                            "public-preview", "public-publish", "public-status", "public-operator", "public-probe", "public-refresh", "teach", "staff-assigner", "telegram", "telegram-check", "jobs", "jobs-retry", "diagnose",
                                            "demo-access", "max-invite", "telegram-design"))
    parser.add_argument("--host", default=os.environ.get("DEAN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=os.environ.get("DEAN_PORT", "8000"))
    parser.add_argument("--to", type=Path, help="Новый файл для backup, demo-access или max-invite.")
    parser.add_argument("--passwords-stdin", action="store_true", help="Прочитать JSON паролей при первом seed.")
    parser.add_argument("--source", default="tusur-timetable", help="Проверенный источник расписания.")
    parser.add_argument("--group", help="Опубликованный идентификатор группы.")
    parser.add_argument("--week", help="Понедельник недели YYYY-MM-DD.")
    parser.add_argument("--mode", choices=("snapshot", "live"), default="snapshot")
    parser.add_argument("--preview", help="Идентификатор сравнения для публикации.")
    parser.add_argument("--login", help="Логин учётной записи для выбранной команды.")
    parser.add_argument("--subject", help="Идентификатор дисциплины для назначения преподавателя.")
    parser.add_argument("--limit", type=int, default=100, help="Предел заданий за запуск.")
    parser.add_argument("--once", action="store_true", help="Один цикл обработки Telegram.")
    args = parser.parse_args()
    path = database_path()
    if args.command in {"demo-access", "max-invite"}:
        from .access_cli import run

        try:
            run(path, args.command, args.login, args.to)
        except (DomainError, ValueError) as error:
            parser.error(str(error))
        except FileExistsError:
            parser.error("Файл уже существует. Укажи новый путь в --to.")
        except OSError:
            parser.error("Не удалось сохранить файл. Проверь, что каталог доступен для записи.")
        print(f"Сохранено в файле с правами 0600 · {args.to}")
        return
    if args.command in {"telegram", "telegram-check", "telegram-design"}:
        from . import jobs, telegram_worker
        from .telegram_transport import BotAPI, TelegramConfig, TransportError, bot_token

        try:
            if args.command in {"telegram-check", "telegram-design"}:
                name = os.getenv("DEAN_TELEGRAM_BOT_USERNAME", "").strip().removeprefix("@").lower()
                transport = BotAPI(TelegramConfig(name=name, token=bot_token()))
                verified_name = transport.preflight()
                if args.command == "telegram-design":
                    transport.configure()
                print(verified_name)
                return
            heartbeat = os.getenv("DEAN_TELEGRAM_HEARTBEAT", "")
            health_file = Path(heartbeat) if heartbeat else None
            if health_file is not None:
                health_file.unlink(missing_ok=True)
            if not path.is_file():
                parser.error("Укажи существующую SQLite.")
            configuration = TelegramConfig.from_env(worker=True)
            with closing(connect(path, create=False)) as db:
                if db.execute("PRAGMA user_version").fetchone()[0] != schema_version():
                    parser.error("Сначала выполни migrate.")
                transport = BotAPI(configuration)
                transport.preflight()
                while True:
                    jobs.run_once(db)
                    result = telegram_worker.tick(db, configuration, transport)
                    print(json.dumps(result,ensure_ascii=False),flush=True)
                    if result.get("poll_error") == "poll_conflict":
                        parser.exit(1, "Telegram: poll_conflict. Останови другой процесс этого бота и проверь webhook.\n")
                    if result.get("poll_error") in {"credentials","blocked","rejected","invalid_response"}:
                        parser.exit(1,"Опрос остановлен. Проверь конфигурацию бота.\n")
                    if health_file is not None and not result.get("poll_error") and not result.get("busy"):
                        health_file.touch()
                    if args.once:
                        if result.get("poll_error"):
                            parser.exit(1,"Telegram: "+result["poll_error"]+"\n")
                        return
                    pause = result.get("retry_after") or (10 if result.get("poll_error") else 2)
                    sleep(max(2,pause))
        except DomainError as error:
            parser.exit(1,error.message+"\n")
        except TransportError as error:
            parser.exit(1,"Telegram: "+error.code+"\n")
        except KeyboardInterrupt:
            print("Опрос остановлен.")
        return
    if args.command == "staff-assigner":
        from . import staff_management
        if not path.is_file() or not args.login:
            parser.error("Укажи существующую базу и --login сотрудника.")
        with closing(connect(path, create=False)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != schema_version():
                parser.error("Сначала выполни migrate.")
            try:
                staff_management.manager_grant(db,args.login)
            except DomainError as error:
                parser.exit(1,error.message+"\n")
        print("Право назначения добавлено в области существующих групп.")
        return
    if args.command == "teach":
        from . import coursework
        if not path.is_file() or not (args.login and args.group and args.subject):
            parser.error("Укажи существующую базу, --login, --group и --subject.")
        with closing(connect(path, create=False)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != schema_version():
                parser.error("Сначала выполни migrate.")
            try:
                coursework.grant_demo(db,args.login,args.group,args.subject)
            except DomainError as error:
                parser.exit(1,error.message+"\n")
        print("Назначение преподавателя сохранено.")
        return
    if args.command == "public-probe":
        from . import public_probe
        try:
            result = public_probe.run(args.source,args.group,args.week)
        except DomainError as error:
            parser.exit(1,error.message+"\n")
        print(json.dumps(result,ensure_ascii=False,indent=2))
        raise SystemExit(0 if result["result"]=="read_parsed" else 1)
    if args.command.startswith("public-"):
        from . import public_refresh, public_schedule, public_sync

        if not path.is_file():
            parser.error("База отсутствует. Сначала обнови существующую установку или создай демонстрационную.")
        with closing(connect(path, create=False)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != schema_version():
                parser.error("Выполни миграцию базы перед работой с публичным расписанием.")
            try:
                if args.command == "public-refresh":
                    result = public_refresh.run_once(db)
                elif args.command == "public-publish":
                    if not args.preview:
                        parser.error("Для public-publish укажи --preview.")
                    result = public_sync.publish(db, args.preview)
                elif args.command == "public-operator":
                    if not args.login:
                        parser.error("Для public-operator укажи --login сотрудника.")
                    public_sync.allow_operator(db, args.login)
                    result = {"operator": args.login}
                else:
                    week = public_schedule.week_scope(args.source, args.group, args.week)
                    if args.command == "public-preview":
                        stage = public_sync.stage_sample if args.mode == "snapshot" else public_sync.stage_live
                        key = stage(db, week)
                        result = {"preview": key, "mode": args.mode,
                                  "diff": public_sync.describe(db, key) if key else "not_modified"}
                    else:
                        latest = db.execute("""
                            SELECT result,started_at,finished_at,retry_at FROM public_attempt
                            WHERE source_id=? AND group_id=? AND monday=? ORDER BY rowid DESC LIMIT 1
                        """, week.key).fetchone()
                        parsed = db.execute("""
                            SELECT max(parsed_at) FROM public_preview
                            WHERE source_id=? AND group_id=? AND monday=? AND provenance='live'
                        """, week.key).fetchone()[0]
                        row = public_sync.week_row(db, week)
                        result = {"source_url": week.url, "scope": week.key, "live_enabled": public_schedule.live_available(week.source_id),
                                  "last_attempt": dict(latest) if latest else None, "last_live_parse": parsed,
                                  "publication": {name: row[name] for name in ("revision", "observed_at", "published_at", "provenance")} if row else None}
            except DomainError as exc:
                parser.exit(1, exc.message + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "backup":
        if args.to is None:
            parser.error("Для backup укажи --to.")
        try:
            backup(path, args.to)
        except (OSError, sqlite3.Error, RuntimeError) as error:
            parser.exit(1, f"Резервная копия не создана. {error}\n")
        print(f"Резервная копия: {args.to.resolve()}")
        return
    if args.command in {"jobs", "jobs-retry", "diagnose"}:
        from . import jobs

        if not path.is_file():
            parser.error("База отсутствует.")
        with closing(connect(path, create=False)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != schema_version():
                parser.error("Сначала выполни migrate.")
            try:
                if args.command == "diagnose":
                    result = jobs.diagnostics(db)
                elif args.command == "jobs":
                    result = jobs.run_once(db, args.limit)
                else:
                    result = {"reset": jobs.retry(db)}
            except DomainError as error:
                parser.exit(1, error.message + "\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if args.command == "reminders":
        from .reminders import refresh_all
        with closing(connect(path, create=False)) as db:
            students, items = refresh_all(db)
        print(f"Напоминания пересчитаны: студентов {students}, активных напоминаний {items}.")
        return
    if args.command == "credentials":
        file = credentials_path(path)
        if not file.is_file():
            parser.error("Локального файла нет. Для новой базы выполни seed. При seed через stdin используй исходный файл с паролями.")
        print(file.read_text(encoding="utf-8"), end="")
        return

    if args.command in {"migrate", "seed"}:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(connect(path)) as db:
            os.chmod(path, 0o600)
            migrate(db)
            if args.command == "seed":
                value = os.getenv("DEAN_DEMO_DATE")
                try:
                    anchor = date.fromisoformat(value) if value else None
                except ValueError:
                    parser.error("DEAN_DEMO_DATE должна быть указана как YYYY-MM-DD.")
                value = os.getenv("DEAN_EXPANSION_DATE")
                try:
                    expansion_date = date.fromisoformat(value) if value else None
                except ValueError:
                    parser.error("DEAN_EXPANSION_DATE должна быть указана как YYYY-MM-DD.")
                try:
                    passwords = json.loads(sys.stdin.read(4097)) if args.passwords_stdin else None
                    if args.passwords_stdin and passwords is None:
                        raise ValueError
                    seed(db, path, anchor, passwords, expansion_date=expansion_date)
                except (ValueError, UnicodeError):
                    parser.error("Ожидается JSON с тремя разными паролями длиной 16–128 символов.")
        print(f"Готово: {args.command} · {path}")
        return

    from .web import create_app

    if args.host not in LOOPBACK:
        parser.error("Локальный serve слушает loopback. Для публичного запуска смотри deploy/DEPLOYMENT.md.")
    if not path.is_file():
        parser.error("База отсутствует. Сначала выполни python3 -m dean seed.")
    with closing(connect(path, create=False)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != schema_version():
            parser.error("Версия базы не подходит для этой сборки. См. инструкцию обновления в README.md.")
    ipv6 = args.host == "::1"
    server_class = IPv6LocalServer if ipv6 else LocalServer
    address = "[::1]" if ipv6 else args.host
    try:
        server = make_server(args.host, args.port, create_app(path), server_class=server_class, handler_class=LocalRequestHandler)
    except OSError as error:
        parser.exit(1, f"Сервер не смог открыть {address}:{args.port}. {error.strerror}\n")
    with server:
        print(f"http://{address}:{server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
