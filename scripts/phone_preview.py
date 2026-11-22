"""Serve the browser demo on one private network interface."""

import argparse
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_address
from os import getenv
import re
from subprocess import CalledProcessError, TimeoutExpired, run
from wsgiref.simple_server import make_server

from dean.__main__ import LocalRequestHandler, LocalServer
from dean.config import WebConfig
from dean.db import database_path
from dean.domain import DomainError
from dean.web import create_app


PRIVATE_NETWORKS = tuple(IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


@dataclass(frozen=True, kw_only=True)
class PhonePreviewConfig(WebConfig):
    interface: IPv4Interface
    port: int

    def __post_init__(self):
        if self.public_origin or self.trust_proxy or not 1 <= self.port <= 65535:
            raise ValueError("Для просмотра с телефона нужен прямой локальный адрес и порт.")
        if not any(self.interface.network.subnet_of(private) for private in PRIVATE_NETWORKS):
            raise ValueError("Подключи Mac к локальной сети с частным IPv4-адресом.")

    @property
    def authority(self):
        return str(self.interface.ip) if self.port == 80 else f"{self.interface.ip}:{self.port}"

    def request_origin(self, env):
        if env["wsgi.url_scheme"] != "http" or env.get("HTTP_HOST") != self.authority:
            raise DomainError(400, "Открой демо по адресу, который указан в окне запуска.")
        try:
            address = ip_address(env.get("REMOTE_ADDR", ""))
        except ValueError:
            raise DomainError(403, "Подключись к той же локальной сети, что и Mac.") from None
        if address not in self.interface.network:
            raise DomainError(403, "Подключись к той же локальной сети, что и Mac.")
        return f"http://{self.authority}"


def attached_interface(name):
    if not re.fullmatch(r"[a-z][a-z0-9]*", name):
        raise ValueError("Укажи имя сетевого интерфейса, например en0.")
    try:
        result = run(("/sbin/ifconfig", name, "inet"), capture_output=True, text=True, check=True, timeout=5)
    except (OSError, CalledProcessError, TimeoutExpired):
        raise ValueError(f"Не удалось прочитать сетевой интерфейс {name}.") from None
    match = re.search(r"^\s*inet\s+(\S+)\s+netmask\s+(0x[0-9a-f]+)", result.stdout, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"У {name} пока нет IPv4-адреса. Подключи Mac к Wi-Fi.")
    address, mask = match.groups()
    return IPv4Interface((address, str(IPv4Address(int(mask, 16)))))


def main():
    parser = argparse.ArgumentParser(description="Цифровой деканат для телефона в локальной сети")
    parser.add_argument("--interface", default="en0")
    parser.add_argument("--port", type=int, default=8098)
    args = parser.parse_args()
    path = database_path()
    if getenv("DEAN_BROWSER_DEMO") != "1" or not path.is_file():
        parser.error("Подготовь демо командой ./start-demo.command phone.")
    try:
        config = PhonePreviewConfig(interface=attached_interface(args.interface), port=args.port)
        server = make_server(str(config.interface.ip), config.port, create_app(path, config),
                             server_class=LocalServer, handler_class=LocalRequestHandler)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Не удалось запустить демо для телефона. {error}\n")
    with server:
        print(f"Демо на телефоне · http://{config.authority}/demo", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
