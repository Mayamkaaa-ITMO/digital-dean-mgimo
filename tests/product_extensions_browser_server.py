"""Loopback-only Chrome fixture with synthetic users, clock and model results."""
import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from sys import argv
from time import sleep
from wsgiref.simple_server import WSGIRequestHandler, make_server

from dean import ai, clock
from dean.__main__ import LocalServer
from dean.access_config import AccessConfig
from dean.config import WebConfig
from dean.db import connect, migrate
from dean.domain import DomainError
from dean.seed import seed
from dean.web import create_app


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_):
        pass


class FixtureModel:
    enabled = True
    daily_limit = 100
    total_limit = 300

    def __init__(self, controls):
        self.controls = controls

    def complete(self, messages, max_tokens, *, model):
        assert model == ai.PHOTO_MODEL
        sleep(0.2)
        mode = json.loads(self.controls.read_text())["provider"]
        if mode == "unavailable":
            raise DomainError(503, "Распознавание временно недоступно.")
        if mode == "unreadable":
            return {"events": []}
        return {"events": [
            {"day": 1, "start": "09:00", "end": "10:30", "subject": "Фото · Картография",
             "kind": "семинар", "room": "1012", "uncertain": False},
            {"day": 3, "start": "14:00", "end": "15:30", "subject": "Фото · Проектная практика",
             "kind": "практика", "room": "", "uncertain": True},
        ]}


def main():
    directory = Path(argv[1]).resolve()
    path, controls = directory / "demo.sqlite3", directory / "controls.json"
    with closing(connect(path)) as db:
        migrate(db)
        if not db.execute("SELECT 1 FROM account LIMIT 1").fetchone():
            at = datetime.fromisoformat(json.loads(controls.read_text())["now"])
            seed(db, path, anchor=at.date())
            db.execute("""
                UPDATE account SET group_id=(SELECT group_id FROM account WHERE username='student-a')
                WHERE username='student-b'
            """)
        group = db.execute("SELECT group_id FROM account WHERE username='student-a'").fetchone()[0]
    app = create_app(path, WebConfig(), access=AccessConfig(), ai_engine=FixtureModel(controls))

    def serve(env, respond):
        current = datetime.fromisoformat(json.loads(controls.read_text())["now"])
        with clock.at(current):
            return app(env, respond)

    with make_server("127.0.0.1", 0, serve, server_class=LocalServer, handler_class=QuietHandler) as server:
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}", "group": group}), flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
