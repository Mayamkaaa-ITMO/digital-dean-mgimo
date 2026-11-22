"""Bounded HTTPS transport for the allowlisted group-week resource."""
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPSConnection, HTTPException
import ipaddress
import re
import socket
import ssl
from time import monotonic
from urllib.parse import urlsplit

MAX_BYTES = 1024 * 1024
TIMEOUT = 9
USER_AGENT = "DigitalDeanPublic/1.0"
NAT64 = (ipaddress.IPv6Network("64:ff9b::/96"), ipaddress.IPv6Network("64:ff9b:1::/48"))


class FetchError(Exception):
    def __init__(self, code, retry_after=None):
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


def allowed_addresses(host):
    info = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(item[4][0] for item in info))
    for value in addresses:
        address = ipaddress.ip_address(value)
        translated = isinstance(address, ipaddress.IPv6Address) and (
            address.ipv4_mapped or address.sixtofour or address.teredo or any(address in net for net in NAT64)
        )
        if (not address.is_global or address.is_multicast or address.is_reserved
                or translated):
            raise FetchError("dns_address_rejected")
    if not addresses:
        raise FetchError("dns")
    return addresses


class PinnedHTTPS(HTTPSConnection):
    def __init__(self, host, address):
        super().__init__(host, port=443, timeout=TIMEOUT, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def retry_time(raw, current):
    if raw:
        try:
            if re.fullmatch(r"[0-9]{1,9}", raw):
                return current + timedelta(seconds=int(raw))
            moment = parsedate_to_datetime(raw)
            return max(moment.astimezone(UTC), current)
        except (ValueError, TypeError, OverflowError):
            pass
    return current + timedelta(hours=1)


def read_week(week, etag):
    target = urlsplit(week.url)
    if (target.scheme != "https" or target.netloc != "timetable.tusur.ru"
            or target.path != week.group["path"] or target.fragment
            or not re.fullmatch(r"/faculties/[a-z0-9-]+/groups/[a-z0-9-]+", target.path)
            or not re.fullmatch(r"week_id=[0-9]{1,8}", target.query)):
        raise FetchError("url_rejected")
    headers = {"Accept": "text/html", "Accept-Encoding": "identity", "User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    connection = None
    try:
        deadline = monotonic() + TIMEOUT
        addresses = allowed_addresses(target.hostname)
        if monotonic() > deadline:
            raise FetchError("timeout")
        connection = PinnedHTTPS(target.hostname, addresses[0])
        connection.request("GET", target.path + "?" + target.query, headers=headers)
        with connection.getresponse() as reply:
            if reply.status == 304:
                return None, etag
            if reply.status in {401, 403}:
                raise FetchError("access_blocked")
            if reply.status in {429, 503}:
                raise FetchError("upstream_busy", retry_time(reply.getheader("Retry-After"), datetime.now(UTC)))
            if 300 <= reply.status < 400:
                raise FetchError("redirect_rejected")
            if reply.status != 200 or reply.headers.get_content_type() != "text/html":
                raise FetchError("unexpected_response")
            if reply.getheader("Content-Encoding", "identity").lower() != "identity":
                raise FetchError("encoding_rejected")
            length = reply.getheader("Content-Length")
            if length and (not length.isdecimal() or int(length) > MAX_BYTES):
                raise FetchError("size_limit")
            chunks, size = [], 0
            while True:
                if monotonic() > deadline:
                    raise FetchError("timeout")
                block = reply.read1(min(16384, MAX_BYTES + 1 - size))
                if not block:
                    break
                size += len(block)
                if size > MAX_BYTES:
                    raise FetchError("size_limit")
                chunks.append(block)
            if length is not None and size != int(length):
                raise FetchError("incomplete_response")
            tag = reply.getheader("ETag", "")
            return b"".join(chunks), tag if re.fullmatch(r'(?:W/)?"[a-zA-Z0-9+/_-]{1,100}"', tag) else None
    except socket.gaierror as exc:
        raise FetchError("dns") from exc
    except TimeoutError as exc:
        raise FetchError("timeout") from exc
    except (OSError, HTTPException) as exc:
        raise FetchError("connection_failed") from exc
    finally:
        if connection:
            connection.close()
