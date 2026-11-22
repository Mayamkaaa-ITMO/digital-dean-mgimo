#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077
port="${DEAN_PORT:-8096}"
case "$port" in ''|*[!0-9]*) echo 'Нужен числовой DEAN_PORT.'; exit 2;; esac
addr="http://127.0.0.1:$port"
fingerprint=$(shasum -a 256 dean/static/field-camera.js | cut -d ' ' -f1)
open_station() {
    open -a "Google Chrome" "$addr/coworking" 2>/dev/null || open "$addr/coworking"
}
same_build() {
    remote=$(curl -fsS --max-time 2 "$addr/static/field-camera.js" 2>/dev/null | shasum -a 256 | cut -d ' ' -f1)
    [ "$remote" = "$fingerprint" ]
}
./prepare-camera.command
if same_build; then
    echo "Стенд работает: $addr/coworking"
    open_station
    exit 0
fi
if curl -s --max-time 2 -o /dev/null "$addr/"; then
    echo "Порт $port занят другой версией. Останови её в её терминале или задай DEAN_PORT=8097."
    exit 1
fi
(
    for attempt in $(seq 1 65); do
        if same_build; then open_station; exit 0; fi
        sleep 1
    done
) &
echo "Оставь это окно открытым во время замера: $addr/coworking"
exec caffeinate -di ./start-campus.command
