#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo 'Скопируй deploy/.env.example в deploy/.env и укажи DEAN_DOMAIN.' >&2
  exit 2
fi
exec docker compose --env-file .env -f compose.yaml "$@"
