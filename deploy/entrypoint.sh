#!/bin/sh
set -eu
umask 077
mkdir -p "$DEAN_LECTURE_LIBRARY_DIR"
python -m dean migrate
exec gunicorn --config deploy/gunicorn.conf.py dean.wsgi:application
