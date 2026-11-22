"""Run the candidate on a temporary HTTPS hostname for the two OAuth providers."""
import argparse
from contextlib import closing
import fcntl
from getpass import getpass
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
from tempfile import NamedTemporaryFile
from threading import Thread
from time import monotonic, sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, urlopen
from wsgiref.simple_server import make_server

from dean import access_cli, ai, coursework, public_sync
from dean.__main__ import LocalRequestHandler, LocalServer
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import ROOT, connect, migrate
from dean.domain import DomainError
from dean.oauth import NoRedirect, VK, callback
from dean.seed import credentials_path
from dean.web import create_app


RUNTIME = ROOT / ".runtime"
STATE = RUNTIME / ("oauth-tunnel-candidate" if os.environ.get("DEAN_OAUTH_CHANNEL") == "candidate" else "oauth-tunnel")
DB = RUNTIME / "oauth-demo" / "demo.sqlite3"
LOGIN = "demo-maxim"
ACCOUNT = DB.with_name("personal-account.json")
ACTIVE = STATE / "active.json"
SETTINGS = STATE / "providers.json"
ORIGIN = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com")


def save(path, data):
    with NamedTemporaryFile("w", dir=path.parent, delete=False) as file:
        json.dump(data, file)
        file.flush()
        os.fsync(file.fileno())
    os.replace(file.name, path)


def load(path):
    return json.loads(path.read_text()) if path.exists() else {}


def prepare():
    os.environ.update(DEAN_DB=str(DB), DEAN_EXT_EVENTS="1", DEAN_EXT_PHOTO_IMPORT="1",
                      DEAN_TELEGRAM_MODE="off")
    for name in ("DEAN_PUBLIC_ORIGIN", "DEAN_SECURE_COOKIES", "DEAN_TRUST_PROXY"):
        os.environ.pop(name, None)
    subprocess.run([sys.executable, "-m", "scripts.prepare_final_demo"], cwd=ROOT,
                   check=True, stdout=subprocess.DEVNULL)
    if not ACCOUNT.exists():
        access_cli.run(DB, "demo-access", LOGIN, ACCOUNT)


def tunnel_binary():
    installed = shutil.which("cloudflared")
    if installed:
        return installed
    for parent in (ROOT.parent / ".runtime/university-five-share",
                   ROOT.parent / "remaining-functions/.runtime"):
        for binary in sorted(parent.glob("*/cloudflared")):
            if binary.is_file() and os.access(binary, os.X_OK):
                return str(binary)
    raise SystemExit("Установи cloudflared и повтори запуск: brew install cloudflared")


def tunnel_origin(child, log):
    deadline = monotonic() + 50
    with log.open() as lines:
        while child.poll() is None and monotonic() < deadline:
            for line in lines:
                match = ORIGIN.search(line)
                if match:
                    return match.group()
            sleep(.2)
    raise SystemExit("Cloudflare не выдал временный адрес. Проверь сеть и журнал " + str(log))


class DemoGateway:
    def __init__(self, db, origin, settings_file):
        self.origin = origin
        self.db = db
        self.settings_file = settings_file
        self.config = WebConfig(origin, trust_proxy=True)
        self.marker = secrets.token_hex(16)
        self.application = self.application_for(AccessConfig())
        self.signature = None

    def application_for(self, settings):
        engine = ai.RouterAI.from_env() if os.environ.get("DEAN_OAUTH_CHANNEL") == "candidate" else ai.RouterAI()
        return create_app(self.db, self.config, access=settings, ai_engine=engine)

    def __call__(self, env, respond):
        if (env["REMOTE_ADDR"] != "127.0.0.1" or env.get("HTTP_X_FORWARDED_PROTO") != "https"
                or "https://" + env.get("HTTP_HOST", "").lower() != self.origin):
            respond("400 Bad Request", [("Content-Type", "text/plain")])
            return [b"Use the assigned HTTPS address."]
        env["wsgi.url_scheme"] = "https"
        env["HTTP_X_REAL_IP"] = env.get("HTTP_CF_CONNECTING_IP", "")
        if env["PATH_INFO"] == "/healthz":
            original = respond
            def respond(status, headers, exc_info=None):
                return original(status, [*headers, ("X-Dean-Instance", self.marker)], exc_info)
        try:
            st = self.settings_file.stat()
            signature = (st.st_ino, st.st_mtime_ns)
        except FileNotFoundError:
            signature = None
        if self.signature != signature:
            values = load(self.settings_file)
            active = values.get("keys", {}) if values.get("origin") == self.origin else {}
            self.application = self.application_for(AccessConfig(**active))
            self.signature = signature
        return self.application(env, respond)


def confirm_local(port, origin, marker):
    with closing(HTTPConnection("127.0.0.1", port, timeout=4)) as client:
        client.request("GET", "/healthz", headers={"Host": urlsplit(origin).netloc, "X-Forwarded-Proto": "https"})
        with client.getresponse() as response:
            if response.status != 200 or response.getheader("X-Dean-Instance") != marker or response.read(10) != b"ok\n":
                raise SystemExit("Локальный процесс не прошёл проверку итоговой сборки.")


def confirm_public(origin, marker, child):
    previous = None
    while child.poll() is None:
        try:
            with urlopen(origin + "/healthz", timeout=4) as response:
                if (response.geturl() == origin + "/healthz" and response.status == 200
                        and response.headers.get("X-Dean-Instance") == marker and response.read(10) == b"ok\n"):
                    print("Внешний HTTPS подтверждён. Можно регистрировать адреса возврата.", flush=True)
                    return
                reason = "Ответ не принадлежит текущей сборке."
        except (URLError, OSError) as error:
            reason = str(getattr(error, "reason", error))
        if reason != previous:
            print("Ожидаю внешний доступ:", reason, "Туннель продолжает работать.", flush=True)
            previous = reason
        sleep(5)


def serve_inherited(fd, origin, marker):
    with closing(connect(DB, create=False)) as db:
        migrate(db)
    if os.environ.get("DEAN_OAUTH_CHANNEL") == "candidate":
        key_file = ROOT.parent / "dean-experience/.runtime/routerai.key"
        if not (os.environ.get("DEAN_AI_API_KEY") or os.environ.get("DEAN_AI_API_KEY_FILE")) and key_file.is_file():
            os.environ["DEAN_AI_API_KEY_FILE"] = str(key_file)
        for feature in ("CAMPUS_PAY", "ROOM_OCCUPANCY", "CAMPUS_DIRECTORY", "LECTURE_STUDIO"):
            os.environ.setdefault("DEAN_EXT_" + feature, "1")
        with closing(connect(DB)) as db:
            from scripts.prepare_final_demo import add_assistant_sample
            add_assistant_sample(db)
            public_sync.allow_operator(db, "staff")
            if os.environ["DEAN_EXT_LECTURE_STUDIO"] == "1":
                coursework.grant_demo(db, "staff", "demo-ai", "databases")
    # The supervisor owns the listener for the lifetime of the tunnel.
    with socket.socket(fileno=fd) as listener:
        address = listener.getsockname()
        if address[0] != "127.0.0.1" or not ORIGIN.fullmatch(origin):
            raise SystemExit("Некорректная конфигурация локального сервера.")
        class GracefulServer(LocalServer):
            daemon_threads = False
        with GracefulServer(address, LocalRequestHandler, bind_and_activate=False) as server:
            server.socket.close()
            server.socket = listener
            server.server_name, server.server_port = address
            server.setup_environ()
            gateway = DemoGateway(DB, origin, SETTINGS)
            gateway.marker = marker
            server.set_app(gateway)
            server.timeout = .3
            stopping = False
            def stop_worker(signum, frame):
                nonlocal stopping
                stopping = True
            signal.signal(signal.SIGTERM, stop_worker)
            while not stopping:
                server.handle_request()


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run():
    binary = tunnel_binary()
    with (STATE / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Стенд уже запускается или работает. Адрес покажет ./oauth-final.command status")
        ACTIVE.unlink(missing_ok=True)
        prepare()
        with make_server("127.0.0.1", 0, None, server_class=LocalServer, handler_class=LocalRequestHandler) as server:
            log = STATE / "cloudflared.log"
            with log.open("w") as output:
                child = subprocess.Popen([binary, "tunnel", "--no-autoupdate", "--url",
                                          f"http://127.0.0.1:{server.server_port}"],
                                         stdout=output, stderr=subprocess.STDOUT)
            worker = None
            reload_request = STATE / "reload-request"
            reload_request.unlink(missing_ok=True)
            try:
                origin = tunnel_origin(child, log)
                marker = secrets.token_hex(16)
                fd = server.fileno()
                def start_worker():
                    with (STATE / "application.log").open("a") as work_log:
                        return subprocess.Popen([sys.executable, "-m", "scripts.oauth_demo", "_worker",
                                                 str(fd), origin, marker], cwd=ROOT, pass_fds=(fd,),
                                                stdout=work_log, stderr=subprocess.STDOUT)
                worker = start_worker()
                confirm_local(server.server_port, origin, marker)
                save(ACTIVE, {"origin": origin, "reload": True})
                print("HTTPS-стенд:", origin + "/login", "\nДомен:", origin.removeprefix("https://"),
                      "\nЯндекс Redirect URI:", callback(origin, "yandex"),
                      "\nVK Redirect URL:", callback(origin, "vk"), flush=True)
                print("Оставь окно открытым. Для настройки провайдеров используй второе окно Terminal.", flush=True)
                Thread(target=confirm_public, args=(origin, marker, child), daemon=True).start()
                while child.poll() is None:
                    if reload_request.exists():
                        reload_request.unlink()
                        stop_process(worker)
                        worker = start_worker()
                        confirm_local(server.server_port, origin, marker)
                        print("Код приложения обновлён. Адрес сохранён.", flush=True)
                    if worker.poll() is not None:
                        raise SystemExit("Сервер приложения остановлен. Подробности в application.log.")
                    sleep(.25)
                raise SystemExit("Туннель остановлен. При новом запуске обнови адреса в кабинетах провайдеров.")
            finally:
                ACTIVE.unlink(missing_ok=True)
                stop_process(child)
                stop_process(worker)


def configure(provider, origin):
    print("Сначала зарегистрируй этот адрес возврата:", callback(origin, provider))
    client = input("Client ID: ").strip()
    if provider == "vk":
        updates = {"vk_client": client}
    else:
        if not sys.stdin.isatty():
            raise SystemExit("Для скрытого ввода Client Secret запусти команду в интерактивном Terminal.")
        updates = {"yandex_client": client, "yandex_secret": getpass("Client Secret (скрытый ввод): ").strip()}
    if not AccessConfig(**updates).enabled(provider, origin):
        raise SystemExit("Проверь скопированные значения из кабинета провайдера.")
    with (STATE / "providers.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if load(ACTIVE).get("origin") != origin:
            raise SystemExit("За время ввода изменился адрес стенда. Проверь текущий Redirect URI.")
        previous = load(SETTINGS)
        keys = previous.get("keys", {}) if previous.get("origin") == origin else {}
        save(SETTINGS, {"origin": origin, "keys": {**keys, **updates}})
    print("Настройки применятся при следующем запросе. Войди по паролю и открой", origin + "/profile/access")


def vk_probe(path, query, fields):
    uri = VK + path + ("?" + urlencode(query) if query else "")
    request = Request(uri, data=urlencode(fields).encode("ascii"), headers={"Accept": "application/json"})
    started = monotonic()
    try:
        try:
            response = build_opener(NoRedirect).open(request, timeout=6)
        except HTTPError as error:
            response = error
        with response:
            result = {"http": response.status, "type": response.headers.get_content_type()}
            body = response.read(65537)
        try:
            obj = json.loads(body) if len(body) <= 65536 else None
        except (UnicodeError, ValueError):
            obj = None
        if isinstance(obj, dict):
            tag = obj.get("error")
            if isinstance(tag, str) and re.fullmatch(r"[a-z_]{1,48}", tag):
                result["oauth_error"] = tag
            description = str(obj.get("error_description", "")).lower()
            for field in ("code_verifier", "device_id", "redirect_uri", "client_id", "code", "token"):
                if re.search(r"\b" + field + r"\b", description):
                    result["error_field"] = field
                    break
    except (URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        result = {"connection_error": type(reason).__name__, "errno": getattr(reason, "errno", None)}
    result["seconds"] = round(monotonic() - started, 2)
    return result


def check_vk():
    cfg = load(SETTINGS)
    origin = load(ACTIVE).get("origin", "")
    client = cfg.get("keys", {}).get("vk_client", "")
    if cfg.get("origin") != origin or not ORIGIN.fullmatch(origin) or not re.fullmatch(r"[0-9]{1,24}", client):
        raise SystemExit("Сначала настрой VK ID на текущем HTTPS-стенде.")
    # Fresh synthetic values only. A normal OAuth rejection proves that VK
    # parsed the form; this test cannot authorize an account.
    params = {"grant_type": "authorization_code", "client_id": client, "redirect_uri": callback(origin, "vk"),
              "state": secrets.token_urlsafe(32), "device_id": secrets.token_urlsafe(24),
              "code_verifier": secrets.token_urlsafe(32)}
    code = {"code": "demo-code-" + secrets.token_urlsafe(24)}
    results = {
        "auth_sdk": vk_probe("/oauth2/auth", params, code),
        "auth_form": vk_probe("/oauth2/auth", {}, {**params, **code}),
        "user_info": vk_probe("/oauth2/user_info", {"client_id": client}, {}),
    }
    save(STATE / "vk-network-check.json", results)
    for name, result in results.items():
        print(name, json.dumps(result))


def preflight(origin):
    """Check the public gateway and then the visible login form."""
    for path in ("/healthz", "/login"):
        url = origin + path
        try:
            with urlopen(url, timeout=12) as response:
                body = response.read(256 * 1024)
                ready = (body == b"ok\n" and bool(response.headers.get("X-Dean-Instance"))
                         if path == "/healthz" else b'form' in body and b'name="password"' in body)
                if response.status != 200 or response.geturl() != url or not ready:
                    raise SystemExit(f"{path}: ответ не соответствует сборке.")
        except (URLError, OSError) as error:
            raise SystemExit(f"{path}: публичный адрес не ответил: {error}") from None
        print("HTTP 200", path)
    print("Страница входа:", origin + "/login")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    modes = cli.add_subparsers(dest="mode")
    modes.add_parser("status")
    modes.add_parser("preflight")
    modes.add_parser("check-vk")
    modes.add_parser("reload")
    worker_cli = modes.add_parser("_worker")
    worker_cli.add_argument("fd", type=int)
    worker_cli.add_argument("origin")
    worker_cli.add_argument("marker")
    modes.add_parser("credentials").add_argument("account", nargs="?", default=LOGIN,
                                                choices=(LOGIN, "student-a", "student-b", "staff"))
    modes.add_parser("configure").add_argument("provider", choices=("yandex", "vk"))
    args = cli.parse_args()
    os.umask(0o077)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.mode == "_worker":
        serve_inherited(args.fd, args.origin, args.marker)
    elif args.mode == "reload":
        if not load(ACTIVE).get("reload"):
            raise SystemExit("Этот процесс запущен без поддержки обновления кода.")
        (STATE / "reload-request").touch(mode=0o600)
        print("Запрошено обновление. Подтверждение появится в окне работающего стенда.")
    elif args.mode == "check-vk":
        check_vk()
    elif args.mode == "credentials":
        account_file = ACCOUNT if args.account == LOGIN else credentials_path(DB)
        if not account_file.exists():
            raise SystemExit("Сначала запусти ./oauth-final.command")
        print("Логин:", args.account, "\nПароль:", load(account_file)[args.account])
    elif args.mode in {"configure", "status", "preflight"}:
        origin = load(ACTIVE).get("origin", "")
        if not ORIGIN.fullmatch(origin):
            raise SystemExit("Стенд ещё не запущен. Выполни ./oauth-final.command и дождись HTTPS-адреса.")
        if args.mode == "preflight":
            preflight(origin)
        elif args.mode == "configure":
            configure(args.provider, origin)
        else:
            print("HTTPS-стенд:", origin + "/login", "\nVK:", callback(origin, "vk"),
                  "\nЯндекс:", callback(origin, "yandex"))
    else:
        def stop(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGHUP, stop)
        run()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except (OSError, DomainError) as error:
        sys.exit(str(error))
