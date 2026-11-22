"""Persistent local inference process. Model weights are never fetched by the running server."""
import atexit
import base64
import json
import os
import select
import subprocess
import threading
from pathlib import Path

from .domain import DomainError

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()
worker = None


def paths():
    local = ROOT / ".runtime" / "field-camera"
    # Reuse Maxim's offline COCO installation when running on the demonstration Mac.
    existing = Path.home() / "Documents/ChatGPT/Хакатон раскар"
    candidates = [(local/"venv/bin/python",local/"yolo11n.pt"),
                  (existing/".venv/bin/python",existing/"yolo11n.pt")]
    if os.getenv("DEAN_FIELD_PYTHON") and os.getenv("DEAN_FIELD_MODEL"):
        candidates.insert(0,(Path(os.environ["DEAN_FIELD_PYTHON"]),Path(os.environ["DEAN_FIELD_MODEL"])))
    return next(((p,m) for p,m in candidates if p.is_file() and m.is_file()),None)


def close():
    global worker
    if worker:
        worker.kill()
        worker.wait()
        worker.stdin.close()
        worker.stdout.close()
        worker = None


atexit.register(close)


def receive(timeout):
    if not select.select([worker.stdout], [], [], timeout)[0]:
        raise TimeoutError
    return json.loads(worker.stdout.readline())


def detect(raw):
    global worker
    config = paths()
    if not config:
        raise DomainError(503,"Установи детектор на Маке командой prepare-camera.command.")
    with LOCK:
        try:
            if not worker or worker.poll() is not None:
                close()
                python, model = config
                worker = subprocess.Popen([str(python),"-u",str(ROOT/"scripts/field_inference.py"),str(model)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
                if not receive(35).get("ready"):
                    raise ValueError
            worker.stdin.write(base64.b64encode(raw)+b"\n")
            worker.stdin.flush()
            result = receive(10)
            if "boxes" not in result:
                raise ValueError
            return result["boxes"]
        except (OSError, ValueError, EOFError, TimeoutError):
            close()
            raise DomainError(503,"Модель не обработала кадр. Проверь установку и повтори запуск камеры.") from None
