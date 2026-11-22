"""Import an audio track from a single public YouTube video."""
import os
import re
import selectors
import shutil
import signal
import subprocess
from tempfile import TemporaryFile
from time import monotonic
from urllib.parse import parse_qs

from .domain import DomainError
from .lecture_analysis import MAX_MINUTES, audio_kind


HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
         "youtube-nocookie.com", "www.youtube-nocookie.com"}
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
ERROR = "YouTube не отдал аудио серверу. Проверь, что видео доступно без входа, или загрузи исходный файл."


def video_url(url):
    try:
        if url.hostname == "youtu.be":
            video = url.path.removeprefix("/")
        elif url.path == "/watch":
            videos = parse_qs(url.query, max_num_fields=32).get("v", [])
            video = videos[0] if len(videos) == 1 else ""
        else:
            match = re.fullmatch(r"/(?:shorts|live|embed)/([\w-]+)", url.path)
            video = match[1] if match else ""
    except ValueError:
        video = ""
    return f"https://www.youtube.com/watch?v={video}" if VIDEO_ID.fullmatch(video) else None


def collect(process, stream, limit, deadline, progress, require_active):
    size, reported = 0, 0
    next_check = 0
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            moment = monotonic()
            remaining = deadline - moment
            if remaining <= 0:
                raise TimeoutError
            if require_active and moment >= next_check:
                require_active()
                next_check = moment + 1
            if not selector.select(min(1, remaining) if require_active else remaining):
                continue
            chunk = os.read(process.stdout.fileno(), 64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise DomainError(413, "Размер аудиодорожки ограничен 64 МиБ.")
            stream.write(chunk)
            received = size // (1024 * 1024)
            if progress and received > reported:
                progress(f"Загружаем звук с YouTube · {received} МиБ")
                reported = received
    return size


def download(source, destination, limit, *, progress=None, require_active=None):
    executable, node = shutil.which("yt-dlp"), shutil.which("node")
    if not executable or not node:
        raise DomainError(503, "На сервере пока недоступна обработка ссылок YouTube. Можно загрузить исходный файл.")
    if progress:
        progress("Ищем аудиодорожку на YouTube")
    deadline = monotonic() + 120
    argv = [executable, "--ignore-config", "--no-plugin-dirs", "--no-cache-dir", "--no-remote-components",
            "--no-js-runtimes", "--js-runtimes", f"node:{node}", "--use-extractors", "youtube",
            "--no-playlist", "--no-progress", "--no-warnings", "--quiet", "--socket-timeout", "8",
            "--retries", "1", "--extractor-retries", "1", "--no-mtime", "--max-filesize", str(limit),
            "--match-filters", f"!is_live & duration <= {MAX_MINUTES * 60}", "--format",
            "bestaudio[protocol=https][ext=m4a]/bestaudio[protocol=https][ext=webm]", "--output", "-", "--", source]
    environment = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"}
    try:
        with destination.open("wb") as output, TemporaryFile() as errors, subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
            env=environment, start_new_session=True,
        ) as process:
            status = None
            try:
                size = collect(process, output, limit, deadline, progress, require_active)
                status = process.wait(timeout=max(0, deadline - monotonic()))
            finally:
                if status is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if status != 0:
                errors.seek(0)
                reason = errors.read(4096).lower()
                if b"unable to download" in reason or b"timed out" in reason:
                    raise DomainError(502, "Сервер не смог загрузить аудио с YouTube. Попробуй позже или загрузи файл.")
                raise DomainError(422, ERROR)
            if not size:
                raise DomainError(422, "YouTube не отдал аудио. Подойдёт доступная запись до 120 минут и 64 МиБ, без прямого эфира.")
    except (TimeoutError, subprocess.TimeoutExpired):
        raise DomainError(504, "YouTube не успел передать аудио. Попробуй ещё раз или загрузи файл.") from None
    except OSError:
        raise DomainError(503, "На сервере не удалось запустить импорт с YouTube. Попробуй позже.") from None
    with destination.open("rb") as media:
        mime, _ = audio_kind(media.read(16))
    return {"video/mp4": "audio/mp4", "video/webm": "audio/webm"}.get(mime, mime)
