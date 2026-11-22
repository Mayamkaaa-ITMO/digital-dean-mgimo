import hashlib
import re
import struct
import unicodedata
import zlib
from email import policy
from email.parser import BytesHeaderParser, HeaderParser
from pathlib import PurePosixPath

from .domain import DomainError, identifier, require, timestamp
from .services import command

MAX_BYTES = 2 * 1024 * 1024
MAX_ACTIVE = 3
MAX_HISTORY = 9
PNG = b"\x89PNG\r\n\x1a\n"


def parse_upload(raw, content_type, extra_fields=()):
    """Parse the small form used by the attachment endpoint."""
    if len(content_type) > 200 or "\r" in content_type or "\n" in content_type:
        raise ValueError
    message = BytesHeaderParser(policy=policy.default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\n\r\n")
    boundary = message.get_boundary()
    if not boundary or not re.fullmatch(r"[a-zA-Z0-9'()+_,./:=?-]{1,70}", boundary):
        raise ValueError
    marker = b"--" + boundary.encode("ascii")
    if not raw.startswith(marker + b"\r\n"):
        raise ValueError
    fields, upload = {}, None
    parts = raw[len(marker) + 2:].split(b"\r\n" + marker, 4 + len(extra_fields))
    for index, part in enumerate(parts):
        if part in {b"--", b"--\r\n"}:
            if upload is None or index != len(parts) - 1:
                raise ValueError
            return fields, upload
        if index:
            if not part.startswith(b"\r\n"):
                raise ValueError
            part = part[2:]
        heading, separator, content = part.partition(b"\r\n\r\n")
        if not separator or len(heading) > 2048:
            raise ValueError
        # Browsers write Unicode filenames as UTF-8 in multipart form headers.
        headers = HeaderParser(policy=policy.default).parsestr(heading.decode("utf-8") + "\r\n\r\n")
        if (headers.defects or len(headers.get_all("Content-Disposition", ())) != 1
                or {name.lower() for name in headers.keys()} - {"content-disposition", "content-type"}):
            raise ValueError
        disposition = headers["Content-Disposition"]
        if disposition.defects or disposition.content_disposition != "form-data":
            raise ValueError
        name = disposition.params.get("name")
        if name == "file" and upload is None:
            upload = (headers.get_filename() or "", content)
        elif (name in {"csrf", "request_id", "version", *extra_fields}
              and name not in fields and len(content) <= 128):
            fields[name] = [content.decode("ascii")]
        else:
            raise ValueError
    raise ValueError


def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload)))


def bounded_png(data):
    if not data.startswith(PNG):
        raise ValueError
    offset, header, compressed, ended = len(PNG), None, bytearray(), False
    expected = 0
    while offset + 12 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        end = offset + 12 + size
        if end > len(data) or not re.fullmatch(rb"[A-Za-z]{4}", kind):
            raise ValueError
        chunk = data[offset + 8:end - 4]
        if zlib.crc32(kind + chunk) != struct.unpack_from(">I", data, end - 4)[0]:
            raise ValueError
        if kind == b"IHDR" and offset == len(PNG) and size == 13:
            width, height, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", chunk)
            if not (1 <= width <= 2048 and 1 <= height <= 2048 and width * height <= 2_000_000
                    and depth == 8 and color in {0, 2, 4, 6} and not (comp or filt or interlace)):
                raise ValueError
            channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
            stride = width * channels + 1
            expected = stride * height
            header = chunk
        elif header is None:
            raise ValueError
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            if size or end != len(data):
                raise ValueError
            ended = True
            break
        elif kind[0] < 97:
            raise ValueError
        offset = end
    if not ended or not compressed:
        raise ValueError
    inflater = zlib.decompressobj()
    pixels = inflater.decompress(compressed, expected + 1)
    if (len(pixels) != expected or not inflater.eof or inflater.unused_data
            or any(pixels[i] > 4 for i in range(0, expected, stride))):
        raise ValueError
    return PNG + png_chunk(b"IHDR", header) + png_chunk(b"IDAT", zlib.compress(pixels)) + png_chunk(b"IEND", b"")


def validated(filename, content):
    name = PurePosixPath(filename.replace("\\", "/")).name.strip()
    if (not name or len(name) > 120 or any(unicodedata.category(c).startswith("C") for c in name)):
        raise DomainError(422, "Укажи короткое имя файла без управляющих символов.")
    if not 0 < len(content) <= MAX_BYTES:
        raise DomainError(413, "Размер вложения должен быть больше нуля и не превышать 2 МиБ.")
    try:
        extension = PurePosixPath(name).suffix.lower()
        if extension == ".png":
            media, content = "image/png", bounded_png(content)
        elif extension == ".txt":
            plain = content.decode("utf-8")
            if any(unicodedata.category(c).startswith("C") and c not in "\r\n\t" for c in plain):
                raise ValueError
            media = "text/plain"
        else:
            raise ValueError
    except (UnicodeError, ValueError, zlib.error) as error:
        raise DomainError(422, "Поддерживаются текст UTF-8 (.txt) и обычные PNG до 2048 × 2048 без чересстрочной развёртки.") from error
    if len(content) > MAX_BYTES:
        raise DomainError(413, "Обработанный файл превышает 2 МиБ.")
    return name, media, content


def files(db, application_id, submission_id=None):
    if submission_id:
        return list(db.execute("""
            SELECT a.id,a.name,length(a.content) AS size FROM application_attachment a
            JOIN application_submission_file f ON f.attachment_id=a.id
            WHERE a.application_id=? AND f.submission_id=? ORDER BY a.rowid
        """, (application_id, submission_id)))
    return list(db.execute("""
        SELECT id,name,length(content) AS size FROM application_attachment
        WHERE application_id=? AND active=1 ORDER BY rowid
    """, (application_id,)))


def change(db, user, aid, version, key, *, upload=None, removing=None):
    from .applications import EDITABLE, applications, version_check
    from .domain import role

    role(user, "student")
    applications(db, user, aid)
    name, media, content = validated(*upload) if upload else ("", "", b"")

    def apply():
        row = applications(db, user, aid)
        version_check(row, version)
        if row["status"] not in EDITABLE:
            raise DomainError(409, "Менять вложения можно в черновике или после возврата на исправление.")
        if removing:
            changed = db.execute("""
                UPDATE application_attachment SET active=0 WHERE id=? AND application_id=? AND active=1
            """, (removing, aid)).rowcount
            if not changed:
                raise DomainError(404, "Вложение недоступно.")
        else:
            total, active = db.execute("""
                SELECT count(*),coalesce(sum(active),0) FROM application_attachment WHERE application_id=?
            """, (aid,)).fetchone()
            if total >= MAX_HISTORY or active >= MAX_ACTIVE:
                raise DomainError(422, "До трёх файлов в подаче и девяти за историю заявления.")
            db.execute("INSERT INTO application_attachment VALUES (?, ?, ?, ?, ?, ?, 1)",
                       (identifier(), aid, name, media, content, timestamp()))
        db.execute("UPDATE application SET version=version+1,updated_at=? WHERE id=?", (timestamp(), aid))
        return aid

    return command(db, user, "application.attachment", key,
                   [aid, version, removing, name, hashlib.sha256(content).hexdigest()], apply)


def download(db, user, fid):
    from .applications import applications

    meta = require(db.execute("SELECT application_id FROM application_attachment WHERE id=?", (fid,)).fetchone())
    applications(db, user, meta["application_id"])
    return require(db.execute("""
        SELECT a.* FROM application_attachment a WHERE a.id=? AND (
            (?=1 AND a.active=1) OR EXISTS (SELECT 1 FROM application_submission_file f WHERE f.attachment_id=a.id)
        )
    """, (fid, user["role"] == "student")).fetchone())
