"""Run live RouterAI calls through WSGI on a temporary demo database (macOS)."""
import argparse
from contextlib import closing
from dataclasses import replace
from datetime import date
from http.client import HTTPSConnection
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from dean.ai import RouterAI
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.pdf import render_document
from dean.seed import credentials_path, seed
from dean.web import create_app

from .test_assisted_dean import PHOTO, upload
from .test_dean import Client

WEEK = date(2030, 9, 2)
SUBJECT = "Прикладная картография"
ROOM = "4102"


class QAConnection(HTTPSConnection):
    def connect(self):
        try:
            super().connect()
        except OSError as error:
            print(f"RouterAI · {type(error).__name__} ({error.errno})", flush=True)
            raise

    def getresponse(self):
        response = super().getresponse()
        print(f"RouterAI · HTTP {response.status}", flush=True)
        return response


def demo_image(folder):
    pdf, png = folder / "schedule.pdf", folder / "schedule.png"
    pdf.write_bytes(render_document("Учебное расписание", [
        f"Понедельник | 09:00–10:30 | {SUBJECT} | лекция | {ROOM}",
    ]))
    subprocess.run(["sips", "-s", "format", "png", str(pdf), "--out", str(png)],
                   check=True, stdout=subprocess.DEVNULL)
    return png.read_bytes()


def check_chat(client):
    fields = {**client.fields("/", "/assistant"),
              "message": "Где найти выданную справку и как скачать её в PDF?"}
    status, _, body = client.request("/assistant", fields, HTTP_ACCEPT="application/json")
    result = json.loads(body)
    ok = status == 200 and result.get("link") in {"/documents", "/requests"} and bool(result.get("text"))
    print(f"DeepSeek · чат и ссылка на справку · {'PASS' if ok else 'FAIL'} · WSGI {status}", flush=True)
    return ok


def check_photo(client, db_path, image):
    fields = {**client.fields(PHOTO, PHOTO), "week": WEEK.isoformat()}
    status, headers, _ = upload(client, PHOTO, fields, image)
    if status != 303:
        print(f"Qwen · предпросмотр фото · FAIL · WSGI {status}", flush=True)
        return False
    draft = headers["Location"]
    preview, _, body = client.request(draft)
    confirm = draft + "/confirm"
    with closing(connect(db_path)) as db:
        draft_row = db.execute("SELECT rows_json FROM calendar_import WHERE id=?", (draft.rsplit("/", 1)[1],)).fetchone()
        rows = json.loads(draft_row["rows_json"]) if draft_row else []
    expected = {
        "subject": SUBJECT, "room": ROOM,
        "start": f"{WEEK}T09:00", "end": f"{WEEK}T10:30",
    }
    matches = len(rows) == 1 and all(rows[0].get(field) == value for field, value in expected.items())
    if preview != 200 or not matches or "Подтвердить личный импорт" not in body:
        print("Qwen · поля распознанной пары или подтверждение черновика · FAIL", flush=True)
        return False
    confirmed, _, _ = client.request(confirm, client.fields(draft, confirm))
    with closing(connect(db_path)) as db:
        saved = db.execute("SELECT count(*) FROM personal_event WHERE title=?", (SUBJECT,)).fetchone()[0]
    schedule, _, body = client.request(f"/schedule?date={WEEK}&view=day")
    ok = confirmed == 303 and saved == 1 and schedule == 200 and SUBJECT in body
    print(f"Qwen · фото → черновик → календарь · {'PASS' if ok else 'FAIL'}", flush=True)
    return ok


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--photo-only", action="store_true")
    args = parser.parse_args()
    engine = replace(RouterAI.from_env(), connect=QAConnection)
    if not engine.enabled:
        raise SystemExit("Укажи DEAN_AI_API_KEY_FILE или DEAN_AI_API_KEY.")
    with TemporaryDirectory() as directory:
        folder = Path(directory)
        path = folder / "demo.sqlite3"
        photo = demo_image(folder)
        with closing(connect(path)) as db:
            migrate(db)
            seed(db, path, WEEK)
        credentials = json.loads(credentials_path(path).read_text())
        client = Client(create_app(path, WebConfig(), ai_engine=engine))
        if client.login("student-a", credentials["student-a"])[0] != 303:
            raise SystemExit("Демо-вход для проверки RouterAI не прошёл.")
        chat_ok = args.photo_only or check_chat(client)
        photo_ok = check_photo(client, path, photo)
        if not (chat_ok and photo_ok):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
