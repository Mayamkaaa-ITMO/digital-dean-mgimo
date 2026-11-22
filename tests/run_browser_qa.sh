#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p .runtime
qa_env=".runtime/browser-qa-venv"
qa_python="$qa_env/bin/python"

{
  if [[ ! -x "$qa_python" ]]; then
    python3 -m venv "$qa_env"
  fi
  if ! "$qa_python" -c 'import playwright.sync_api' 2>/dev/null; then
    "$qa_python" -m pip install --disable-pip-version-check --retries 1 --timeout 10 'playwright>=1.49,<2'
  fi
  if "$qa_python" -c 'import webauthn' 2>/dev/null ||
     "$qa_python" -m pip install --disable-pip-version-check --retries 1 --timeout 10 -r requirements-auth.txt; then
    "$qa_python" -m tests.browser_seamless_qa --chrome --passkey "$@"
  else
    printf '%s\n' 'SKIP · WebAuthn · зависимость не установилась. Запускаю проверку парольного входа и кабинета.'
    "$qa_python" -m tests.browser_seamless_qa --chrome "$@"
  fi
} 2>&1 | tee .runtime/browser-qa-v29.log
