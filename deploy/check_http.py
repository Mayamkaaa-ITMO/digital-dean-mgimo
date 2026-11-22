import http.client
import os
from contextlib import closing
from urllib.parse import urlsplit


origin = urlsplit(os.environ["DEAN_PUBLIC_ORIGIN"])
with closing(http.client.HTTPConnection("127.0.0.1", 8000, timeout=3)) as client:
    client.request("GET", "/healthz", headers={"Host": origin.netloc, "X-Forwarded-Proto": "https"})
    response = client.getresponse()
    if response.status != 200 or response.read() != b"ok\n":
        raise SystemExit(1)
