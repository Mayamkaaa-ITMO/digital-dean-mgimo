#!/bin/sh
set -eu
cd "$(dirname "$0")"
if python3 -c 'from dean.field_detector import paths; raise SystemExit(paths() is None)'; then
    echo 'Локальная модель и Python найдены.'
else
    dest="$PWD/.runtime/field-camera"
    mkdir -p "$dest"
    runner="${DEAN_FIELD_INSTALL_PYTHON:-python3.12}"
    if ! command -v "$runner" >/dev/null; then echo 'Установи Python 3.12 или задай DEAN_FIELD_INSTALL_PYTHON.'; exit 1; fi
    "$runner" -m venv "$dest/venv"
    "$dest/venv/bin/python" -m pip install 'ultralytics>=8.3,<9'
    temp_model=$(mktemp "$dest/model.XXXXXX")
    trap 'rm -f "$temp_model"' EXIT
    curl --fail --location --proto '=https' 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt' -o "$temp_model"
    expected=0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1
    actual=$(shasum -a 256 "$temp_model" | cut -d ' ' -f1)
    if [ "$actual" != "$expected" ]; then echo 'Контрольная сумма модели отличается от проверенной.'; exit 1; fi
    mv "$temp_model" "$dest/yolo11n.pt"
fi
exec python3 -m scripts.check_field_camera
