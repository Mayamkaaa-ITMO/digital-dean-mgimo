"""Bounded recording uploads and imports from public HTTPS sources."""
from contextlib import contextmanager
from email import policy
from email.parser import BytesHeaderParser, HeaderParser
from html.parser import HTMLParser
from http.client import HTTPSConnection, HTTPException, IncompleteRead
from io import BytesIO
from ipaddress import ip_address
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.parse import quote, unquote, urldefrag, urljoin, urlsplit
import os
import re
import socket
import ssl

from . import lecture_youtube
from .domain import DomainError
from .lecture_analysis import audio_kind


MAX_BYTES = 64 * 1024 * 1024
MAX_PAGE_BYTES = 1024 * 1024
MAX_VIDEO_CHOICES = 5
CHUNK = 32 * 1024
VIDEO_TYPES = {"video/mp4", "video/webm"}
DEFAULT_MEDIA_HOSTS = frozenset({"interactive-examples.mdn.mozilla.net", "media.w3.org", "upload.wikimedia.org"})
COMMONS = "commons.wikimedia.org"
COMMONS_MEDIA = "upload.wikimedia.org"
COMMONS_REDIRECT = "/wiki/Special:FilePath/"
W3C_PAGE = "www.w3.org"
W3C_MEDIA = "media.w3.org"
W3C_VIDEO_PATHS = ("/WAI/videos/", "/WAI/perspective-videos/")
HTML_TYPES = {"text/html", "application/xhtml+xml"}


def storage(db):
    library = os.environ.get("DEAN_LECTURE_LIBRARY_DIR")
    root = Path(library) if library else Path(db.execute("PRAGMA database_list").fetchone()[2]).parent
    root = root / ".recordings"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    return root


@contextmanager
def temporary(root):
    with NamedTemporaryFile(dir=root, prefix=".incoming-", delete=False) as stream:
        path = Path(stream.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def media_type(path):
    with path.open("rb") as stream:
        mime, _ = audio_kind(stream.read(16))
    if mime not in VIDEO_TYPES:
        raise DomainError(422, "Выбери видео MP4 или WebM со звуковой дорожкой.")
    return mime


class Multipart:
    """Read form parts with a rolling buffer, without loading the video into memory."""
    def __init__(self, stream, size):
        self.stream = stream
        self.left = size
        self.buffer = b""

    def fill(self):
        if not self.left:
            raise ValueError
        chunk = self.stream.read(min(CHUNK, self.left))
        if not chunk:
            raise ValueError
        self.left -= len(chunk)
        self.buffer += chunk

    def take(self, size):
        while len(self.buffer) < size:
            self.fill()
        value, self.buffer = self.buffer[:size], self.buffer[size:]
        return value

    def until(self, marker, write, limit, *, boundary=False):
        count = 0
        while True:
            edge = self.buffer.find(marker)
            literal = False
            if edge >= 0:
                stop = edge
                if boundary:
                    suffix = edge + len(marker)
                    while len(self.buffer) < suffix + 2:
                        self.fill()
                    if self.buffer[suffix:suffix + 2] not in {b"--", b"\r\n"}:
                        literal, stop = True, suffix
            else:
                stop = max(0, len(self.buffer) - len(marker) + 1)
            count += stop
            if count > limit:
                raise DomainError(413, "Файл или поле превышает допустимый размер.")
            write(self.buffer[:stop])
            self.buffer = self.buffer[stop:]
            if literal:
                continue
            if edge >= 0:
                self.buffer = self.buffer[len(marker):]
                return
            self.fill()


def read_upload(env, path, valid_csrf):
    content_type = env.get("CONTENT_TYPE", "")
    size = env.get("CONTENT_LENGTH", "")
    if not size.isascii() or not size.isdecimal() or len(size) > 8 or int(size) > MAX_BYTES + 8192:
        raise DomainError(413, "Размер видео ограничен 64 МиБ.")
    if len(content_type) > 200 or "\r" in content_type or "\n" in content_type:
        raise DomainError(400, "Не удалось прочитать форму загрузки.")
    try:
        encoding = BytesHeaderParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode("ascii") + b"\r\n\r\n")
        boundary = encoding.get_boundary()
        if (encoding.get_content_type() != "multipart/form-data"
                or not boundary or not re.fullmatch(r"[a-zA-Z0-9'()+_,./:=?-]{1,70}", boundary)):
            raise ValueError
        marker = b"--" + boundary.encode("ascii")
        reader = Multipart(env["wsgi.input"], int(size))
        if reader.take(len(marker) + 2) != marker + b"\r\n":
            raise ValueError
        fields, uploaded = {}, False
        for _ in range(8):
            heading = bytearray()
            reader.until(b"\r\n\r\n", heading.extend, 2048)
            headers = HeaderParser(policy=policy.default).parsestr(heading.decode("utf-8") + "\r\n\r\n")
            disposition = headers["Content-Disposition"]
            if (headers.defects or len(headers.get_all("Content-Disposition", [])) != 1
                    or disposition.defects or disposition.content_disposition != "form-data"):
                raise ValueError
            name = disposition.params.get("name")
            if name == "file" and not uploaded:
                if not valid_csrf(fields.get("csrf", "")):
                    raise DomainError(403, "Срок действия формы истёк. Открой страницу заново.")
                with path.open("wb") as stream:
                    reader.until(b"\r\n" + marker, stream.write, MAX_BYTES, boundary=True)
                uploaded = True
            elif name in {"csrf", "request_id", "title", "scope", "lecture", "cloud", "model"} and name not in fields:
                value = bytearray()
                reader.until(b"\r\n" + marker, value.extend, 600, boundary=True)
                fields[name] = value.decode("utf-8")
            else:
                raise ValueError
            end = reader.take(2)
            if end == b"--":
                remaining = reader.left + len(reader.buffer)
                if uploaded and (not remaining or remaining == 2 and reader.take(2) == b"\r\n"):
                    return fields
                raise ValueError
            if end != b"\r\n":
                raise ValueError
    except (UnicodeError, ValueError):
        pass
    raise DomainError(400, "Не удалось прочитать форму загрузки.")


def parsed_url(value):
    try:
        if not 1 <= len(value) <= 1500 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError
        url = urlsplit(value)
        host = url.hostname or ""
        if (url.scheme != "https" or url.port not in (None, 443) or url.username is not None or url.password is not None
                or "\\" in value or not re.fullmatch(r"(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}", host)):
            raise ValueError
        return url._replace(path=quote(url.path, safe="/!$&'()*+,;=:@%"), query=quote(url.query, safe="/?!$&'()*+,;=:@%"), fragment="")
    except ValueError:
        raise DomainError(422, "Укажи корректную HTTPS-ссылку на видео.") from None


def media_hosts():
    raw = os.environ.get("DEAN_LECTURE_IMPORT_HOSTS")
    return DEFAULT_MEDIA_HOSTS if raw is None else {name.strip().lower() for name in raw.split(",") if name.strip()}


def trusted_host(host):
    return host in media_hosts()


def public_links_enabled():
    default = "0" if "DEAN_LECTURE_IMPORT_HOSTS" in os.environ else "1"
    return bool(media_hosts()) and os.environ.get("DEAN_LECTURE_PUBLIC_LINKS", default) == "1"


def recording_source(source):
    url = parsed_url(source)
    if url.hostname == urlsplit(os.environ.get("DEAN_PUBLIC_ORIGIN", "")).hostname:
        return None
    if url.hostname == COMMONS and trusted_host(COMMONS_MEDIA):
        path = unquote(url.path)
        for prefix in ("/wiki/File:", COMMONS_REDIRECT):
            if path.startswith(prefix):
                filename = path[len(prefix):]
                if re.fullmatch(r"[^/\\\x00-\x1f]{1,240}[.](?:mp4|webm)", filename, re.IGNORECASE):
                    return f"https://{COMMONS}{COMMONS_REDIRECT}{quote(filename, safe='')}"
    if url.hostname == W3C_PAGE and trusted_host(W3C_MEDIA) and url.path.startswith(W3C_VIDEO_PATHS):
        return url.geturl()
    if url.hostname in lecture_youtube.HOSTS and (trusted_host(url.hostname) or public_links_enabled()):
        return lecture_youtube.video_url(url)
    return url.geturl() if trusted_host(url.hostname) or public_links_enabled() else None


class PageVideos(HTMLParser):
    """Find media the publisher exposes in the page markup."""

    def __init__(self, url):
        super().__init__()
        self.url = url
        self.video_depth = 0
        self.sources = []
        self.downloads = []
        self.embedded = []
        self.metadata = []
        self.json_ld = False

    def candidate(self, choices, value, *, direct=False):
        if not value or len(value) > 1500 or len(choices) >= MAX_VIDEO_CHOICES:
            return
        try:
            url = urldefrag(urljoin(self.url, value.strip())).url
            if url == self.url or not recording_source(url):
                return
            if direct and not unquote(urlsplit(url).path).lower().endswith((".mp4", ".webm")):
                return
        except (ValueError, DomainError):
            return
        if url not in choices:
            choices.append(url)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "video":
            self.video_depth += 1
            self.candidate(self.sources, attrs.get("src"))
            video_id = attrs.get("data-youtube-id") or ""
            if lecture_youtube.VIDEO_ID.fullmatch(video_id):
                self.candidate(self.embedded, f"https://www.youtube.com/watch?v={video_id}")
        elif tag == "source" and self.video_depth:
            kind = (attrs.get("type") or "").partition(";")[0].lower().strip()
            if not kind or kind in VIDEO_TYPES:
                self.candidate(self.sources, attrs.get("src"))
        elif tag == "meta":
            name = (attrs.get("property") or attrs.get("name") or "").lower()
            if name in {"og:video", "og:video:url", "og:video:secure_url"}:
                self.candidate(self.metadata, attrs.get("content"))
        elif tag == "a":
            self.candidate(self.downloads, attrs.get("href"), direct=True)
        elif tag == "script":
            self.json_ld = attrs.get("type") == "application/ld+json"

    def handle_endtag(self, tag):
        if tag == "video" and self.video_depth:
            self.video_depth -= 1
        elif tag == "script":
            self.json_ld = False

    def handle_data(self, value):
        if not self.json_ld or len(value) > 65536 or len(self.metadata) >= MAX_VIDEO_CHOICES:
            return
        try:
            data = json.loads(value)
        except (ValueError, RecursionError):
            return
        objects = data if isinstance(data, list) else [data]
        for row in objects[:24]:
            children = row.get("@graph", []) if isinstance(row, dict) else []
            for item in [row, *(children[:24] if isinstance(children, list) else [])]:
                if not isinstance(item, dict):
                    continue
                kind = item.get("@type")
                content = item.get("contentUrl")
                if isinstance(content, str) and (kind == "VideoObject" or
                                                isinstance(kind, list) and "VideoObject" in kind):
                    self.candidate(self.metadata, content)

    def choices(self):
        return list(dict.fromkeys(self.sources + self.metadata + self.downloads + self.embedded))[:MAX_VIDEO_CHOICES]


class PublicHTTPSConnection(HTTPSConnection):
    def __init__(self, host, timeout):
        super().__init__(host, timeout=timeout)
        results = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        addresses = [ip_address(info[4][0]) for info in results]
        if not addresses or any(not a.is_global or a.is_multicast or a.is_reserved or a.is_link_local
                                or getattr(a, "ipv4_mapped", None) or getattr(a, "sixtofour", None)
                                or getattr(a, "teredo", None) for a in addresses):
            raise DomainError(422, "Источник записи должен быть доступен по публичному HTTPS.")
        # Pin the checked addresses and preserve the original Host, SNI and certificate validation.
        self._create_connection = self._connect_public
        self.public_addresses = tuple(dict.fromkeys(str(a) for a in addresses))

    def _connect_public(self, address, timeout, source_address, **kwargs):
        stop = monotonic() + timeout
        last_error = None
        for host in self.public_addresses:
            remaining = stop - monotonic()
            if remaining <= 0:
                break
            try:
                return socket.create_connection((host, address[1]), timeout=min(3, remaining), source_address=source_address)
            except OSError as error:
                last_error = error
        if last_error:
            raise last_error
        raise TimeoutError


@contextmanager
def remote_response(source, deadline):
    try:
        for _ in range(4):
            source = recording_source(source)
            if not source:
                raise DomainError(422, "Источник ещё не подключён для обработки.")
            url = parsed_url(source)
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            connection = PublicHTTPSConnection(url.hostname, min(10, remaining))
            try:
                path = url.path or "/"
                if url.query:
                    path += "?" + url.query
                agent = "DigitalDean-LectureImport/1.0"
                origin = os.environ.get("DEAN_PUBLIC_ORIGIN", "")
                if url.hostname in {COMMONS, COMMONS_MEDIA} and re.fullmatch(r"https://[a-zA-Z0-9.-]+(?::\d+)?", origin):
                    agent += f" (+{origin}/)"
                connection.request("GET", path, headers={"Accept": "video/mp4, video/webm, text/html, application/xhtml+xml, application/octet-stream",
                                                          "Accept-Encoding": "identity",
                                                          "User-Agent": agent})
                with connection.getresponse() as response:
                    status = response.status
                    if status in {301, 302, 303, 307, 308}:
                        location = response.getheader("Location")
                        if not location:
                            raise DomainError(502, "Источник вернул перенаправление без адреса записи.")
                        try:
                            target = recording_source(urljoin(source, location))
                        except (ValueError, DomainError):
                            target = None
                        if not target:
                            raise DomainError(422, "Источник перенаправил на площадку, которая не подключена для обработки.")
                        source = target
                        continue
                    if status == 401:
                        raise DomainError(422, "Источник требует разрешения на доступ к видео.")
                    if status == 403:
                        name = "Сервер Wikimedia" if url.hostname in {COMMONS, COMMONS_MEDIA} else "Источник"
                        raise DomainError(422, f"{name} отклонил запрос на скачивание (HTTP 403). Открой оригинал или загрузи файл.")
                    if status in {404, 410}:
                        raise DomainError(422, "Источник не нашёл запись по этому адресу. Проверь ссылку или загрузи файл.")
                    if status == 429 or status >= 500:
                        raise DomainError(503, "Источник временно не отдаёт запись. Попробуй позже или загрузи файл.")
                    content_type = response.getheader("Content-Type", "").partition(";")[0].strip().lower()
                    if (status != 200
                            or response.getheader("Content-Encoding", "identity").lower() != "identity"):
                        raise DomainError(422, "Источник не вернул доступную страницу или видео.")
                    yield source, response, content_type
                    return
            finally:
                connection.close()
    except TimeoutError:
        raise DomainError(504, "Источник не успел передать видео. Попробуй ещё раз или загрузи файл.") from None
    except socket.gaierror:
        raise DomainError(502, "Сервер не смог найти источник видео. Попробуй позже или загрузи файл.") from None
    except ssl.SSLError:
        raise DomainError(502, "Не удалось установить защищённое соединение с источником видео. Загрузи исходный файл.") from None
    except IncompleteRead:
        raise DomainError(502, "Источник оборвал скачивание. Попробуй повторить или загрузи файл.") from None
    except (OSError, HTTPException, UnicodeError):
        raise DomainError(422, "Видео загрузить не удалось. Проверь ссылку и доступ к записи.") from None
    raise DomainError(422, "Ссылка ведёт через слишком много перенаправлений. Укажи ссылку на оригинал или загрузи файл.")


def copy_remote(response, destination, limit, deadline, size_error, progress=None, *, require_active=None):
    size = 0
    reported = 0
    next_check = 0
    if response.length is not None and response.length > limit:
        raise DomainError(413, size_error)
    while True:
        moment = monotonic()
        if moment >= deadline:
            raise TimeoutError
        if require_active and moment >= next_check:
            require_active()
            next_check = moment + 1
        chunk = response.read1(CHUNK)
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise DomainError(413, size_error)
        destination.write(chunk)
        received = size // (1024 * 1024)
        if progress and received > reported:
            progress(f"Загружаем видео · {received} МиБ")
            reported = received
    if response.length not in (0, None):
        raise DomainError(502, "Источник оборвал скачивание. Попробуй повторить или загрузи файл.")


def download_media(response, content_type, destination, deadline, progress, require_active):
    if content_type not in VIDEO_TYPES | {"application/octet-stream"}:
        raise DomainError(422, "Источник не отдаёт файл MP4 или WebM.")
    if progress:
        progress("Загружаем видео")
    with destination.open("wb") as stream:
        copy_remote(response, stream, MAX_BYTES, deadline, "Размер видео ограничен 64 МиБ.", progress,
                    require_active=require_active)
    return media_type(destination)


def download(source, destination, *, progress=None, require_active=None):
    parsed = parsed_url(source)
    if parsed.hostname in lecture_youtube.HOSTS:
        if not lecture_youtube.video_url(parsed):
            raise DomainError(422, "Укажи ссылку на одно видео YouTube.")
        video = recording_source(source)
        if not video:
            raise DomainError(422, "Импорт с YouTube отключён в настройках этого кабинета.")
        return lecture_youtube.download(video, destination, MAX_BYTES, progress=progress, require_active=require_active)
    deadline = monotonic() + 90
    if progress:
        progress("Проверяем источник видео")
    with remote_response(source, deadline) as (page, response, content_type):
        if content_type not in HTML_TYPES:
            return download_media(response, content_type, destination, deadline, progress, require_active)
        if progress:
            progress("Ищем видео на странице")
        markup = BytesIO()
        copy_remote(response, markup, MAX_PAGE_BYTES, deadline, "Страница источника превышает 1 МиБ.",
                    require_active=require_active)
    parser = PageVideos(page)
    parser.feed(markup.getvalue().decode("utf-8", errors="replace"))
    error = DomainError(422, "На странице не найден доступный MP4, WebM или ролик YouTube. Укажи прямую ссылку на видео или загрузи файл.")
    for choice in parser.choices():
        try:
            candidate = parsed_url(choice)
            video = lecture_youtube.video_url(candidate) if candidate.hostname in lecture_youtube.HOSTS else None
            if video:
                return lecture_youtube.download(video, destination, MAX_BYTES, progress=progress,
                                                require_active=require_active)
            with remote_response(choice, deadline) as (_, response, content_type):
                return download_media(response, content_type, destination, deadline, progress, require_active)
        except DomainError as exc:
            if exc.status == 409:
                raise
            error = exc
    raise error
