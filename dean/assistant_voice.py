"""Short microphone recordings are transcribed with the local Whisper model."""
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore

from . import lecture_analysis
from .domain import DomainError


PATH = "/assistant/voice"
MAX_BYTES = 2 * 1024 * 1024
MAX_MESSAGE = 700
_worker = BoundedSemaphore()


def local_model():
    model = os.getenv("DEAN_WHISPER_MODEL", "")
    if model and Path(model).is_file() and shutil.which("ffmpeg") and shutil.which("whisper-cli"):
        return model
    return ""


def transcribe(body):
    if len(body) > MAX_BYTES:
        raise DomainError(413, "Запись занимает больше 2 МиБ. Попробуй надиктовать короче.")
    if not body:
        raise DomainError(422, "Запись пустая. Нажми на микрофон и попробуй снова.")
    if not (model := local_model()):
        raise DomainError(503, "Распознавание речи пока недоступно. Можно отправить текст.")
    if not _worker.acquire(blocking=False):
        raise DomainError(429, "Распознавание занято. Попробуй через несколько секунд.")
    try:
        with TemporaryDirectory(prefix="dean-dictation-") as directory:
            folder = Path(directory)
            try:
                wav = lecture_analysis.normalize([body], folder, max_seconds=35, timeout=8)
            except DomainError as error:
                if error.status == 413:
                    raise DomainError(413, "Запись слишком длинная. Надиктуй до 30 секунд.") from None
                raise DomainError(422, "Не получилось прочитать звук. Попробуй записать заново.") from None
            text = lecture_analysis.whisper_text(wav, folder / "text", model, threads=3, timeout=60, quick=True)
    finally:
        _worker.release()
    text = re.sub(r"\[[^\]\n]{1,70}\]", " ", text)
    text = " ".join(text.split())[:MAX_MESSAGE].rstrip()
    if not any(char.isalnum() for char in text):
        raise DomainError(422, "Не получилось разобрать речь. Попробуй ещё раз в тихом месте.")
    return text
