"""Serve one real HTTP request over an inherited local test socket."""

import socket
import sys
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer

from dean.wsgi import application


if __name__ == "__main__":
    with socket.socket(fileno=int(sys.argv[1])) as connection:
        with WSGIServer(("127.0.0.1", 80), WSGIRequestHandler, bind_and_activate=False) as server:
            server.server_name = "127.0.0.1"
            server.server_port = 80
            server.setup_environ()
            server.set_app(application)
            WSGIRequestHandler(connection, ("127.0.0.1", 32000), server)
