#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077

profile="${DEAN_DEMO_NAME:-interface-demo}"
case "$profile" in
    ''|*[!a-zA-Z0-9-]*) echo 'Укажи имя тестовой базы латиницей, цифрами и через дефис.' >&2; exit 2 ;;
esac
export DEAN_BROWSER_DEMO=1 DEAN_CAMPUS_LAB=1 DEAN_DEMO_PROFILE=experiments
export DEAN_PORT="${DEAN_PORT:-8098}"
export DEAN_DB="$PWD/.runtime/$profile/demo.sqlite3"
export DEAN_LECTURE_LIBRARY_DIR="${DEAN_LECTURE_LIBRARY_DIR:-$PWD/.runtime/$profile/lectures}"
export DEAN_EXT_EVENTS=1 DEAN_EXT_PHOTO_IMPORT=1 DEAN_EXT_CAMPUS_PAY=1
export DEAN_EXT_ROOM_OCCUPANCY=1 DEAN_EXT_CAMPUS_DIRECTORY=1 DEAN_EXT_LECTURE_STUDIO=1
export DEAN_TELEGRAM_MODE=off
runner="${DEAN_PYTHON:-python3}"

mkdir -p .runtime
if [ "$(uname -s)" = Darwin ] && [ ! -x .runtime/campus-ocr ] && command -v clang >/dev/null 2>&1; then
    ocr_build=$(mktemp .runtime/ocr-build.XXXXXX)
    if clang -fobjc-arc -Wno-deprecated-declarations -framework Foundation -framework Vision \
        -framework CoreGraphics scripts/campus_ocr.m -o "$ocr_build"; then
        chmod 700 "$ocr_build"
        mv "$ocr_build" .runtime/campus-ocr
    else
        rm -f "$ocr_build"
    fi
fi
"$runner" -m scripts.prepare_final_demo
mkdir -p "$DEAN_LECTURE_LIBRARY_DIR"
"$runner" -m scripts.campus_lab_setup
"$runner" -m dean public-operator --login staff >/dev/null

stop_demo() {
    kill "$server_pid" "$worker_pid" 2>/dev/null || true
    wait "$server_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
}

serve_demo() {
    export DEAN_LECTURE_WORKER=1
    "$runner" -m scripts.lecture_worker &
    worker_pid=$!
    "$runner" "$@" &
    server_pid=$!
    trap stop_demo EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
    wait "$server_pid"
}

case "${1:-serve}" in
    prepare) exit 0 ;;
    credentials) exec "$runner" -m dean credentials ;;
    serve)
        printf '%s\n' "Демо: http://127.0.0.1:$DEAN_PORT/demo"
        "$runner" -m dean credentials
        serve_demo -m dean serve --host 127.0.0.1 --port "$DEAN_PORT" ;;
    phone)
        serve_demo -m scripts.phone_preview --interface "${2:-en0}" --port "$DEAN_PORT" ;;
    *) echo 'Режимы: serve, phone, prepare, credentials' >&2; exit 2 ;;
esac
