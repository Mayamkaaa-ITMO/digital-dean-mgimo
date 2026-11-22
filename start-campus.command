#!/bin/sh
set -eu
cd "$(dirname "$0")"
umask 077
export DEAN_CAMPUS_LAB=1 DEAN_DEMO_PROFILE=experiments DEAN_PORT="${DEAN_PORT:-8096}"
export DEAN_DB="$PWD/.runtime/experiments-demo/demo.sqlite3"
export DEAN_EXT_EVENTS=1 DEAN_EXT_PHOTO_IMPORT=1 DEAN_EXT_CAMPUS_PAY=1 DEAN_EXT_ROOM_OCCUPANCY=1 DEAN_EXT_CAMPUS_DIRECTORY=1 DEAN_EXT_LECTURE_STUDIO=1
export DEAN_TELEGRAM_MODE=off
if [ -z "${OPENROUTER_API_KEY:-}${OPENROUTER_API_KEY_FILE:-}${DEAN_AI_API_KEY:-}${DEAN_AI_API_KEY_FILE:-}" ] && [ -r ../dean-experience/.runtime/routerai.key ]; then
    export DEAN_AI_API_KEY_FILE="$PWD/../dean-experience/.runtime/routerai.key"
fi
if [ -z "${DEAN_WHISPER_MODEL:-}" ] && [ -r "$HOME/Documents/Проект по умолчанию/rascar_transcript/ggml-large-v3-turbo-q5_0.bin" ]; then
    export DEAN_WHISPER_MODEL="$HOME/Documents/Проект по умолчанию/rascar_transcript/ggml-large-v3-turbo-q5_0.bin"
fi
runner="${DEAN_PYTHON:-python3}"
if [ -z "${DEAN_OCR_PYTHON:-}" ] && [ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]; then
    export DEAN_OCR_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
fi
mkdir -p .runtime
if [ "$(uname -s)" = Darwin ] && [ ! -x .runtime/campus-ocr ] && command -v clang >/dev/null 2>&1; then
    temp_bin=$(mktemp .runtime/ocr-build.XXXXXX)
    if clang -fobjc-arc -Wno-deprecated-declarations -framework Foundation -framework Vision -framework CoreGraphics scripts/campus_ocr.m -o "$temp_bin"; then
        chmod 700 "$temp_bin" && mv "$temp_bin" .runtime/campus-ocr
    else
        rm -f "$temp_bin"
    fi
fi
"$runner" -m scripts.prepare_final_demo
"$runner" -m scripts.campus_lab_setup
case "${1:-serve}" in
    prepare) exit 0 ;;
    credentials) exec "$runner" -m dean credentials ;;
    serve) "$runner" -m dean credentials; echo "Кампус: http://127.0.0.1:$DEAN_PORT/campus-lab"; exec "$runner" -m dean serve --host 127.0.0.1 --port "$DEAN_PORT" ;;
    *) echo 'Режимы: serve, prepare, credentials' >&2; exit 2 ;;
esac
