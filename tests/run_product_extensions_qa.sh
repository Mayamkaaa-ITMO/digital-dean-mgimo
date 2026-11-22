#!/bin/sh
set -eu
umask 077
cd "$(dirname "$0")/.."
report_dir=${DEAN_EXT_QA_LOG_DIR:-"$PWD/../.runtime/product-extensions-baseline"}
mkdir -p "$report_dir"
python=${DEAN_PYTHON:-python3}
export PYTHONDONTWRITEBYTECODE=1
run_chrome() {
    browser_result=0
    node tests/browser_product_extensions_qa.cjs >"$report_dir/chrome-ext.log" 2>&1 || browser_result=$?
    cat "$report_dir/chrome-ext.log"
}
rerun="$report_dir/retry-chrome"
started="$report_dir/started-chrome"
touch "$started"
run_chrome
if [ "${1:-}" = "--watch" ]; then
    attempts=0
    while [ "$browser_result" -ne 0 ] && [ "$attempts" -lt 180 ]; do
        if [ "$rerun" -nt "$started" ]; then
            touch "$started"
            run_chrome
        fi
        attempts=$((attempts + 1))
        sleep 2
    done
fi

live_result=0
if [ -n "${DEAN_AI_API_KEY:-}${DEAN_AI_API_KEY_FILE:-}" ]; then
    DEAN_EXT_PHOTO_IMPORT=1 "$python" -m tests.routerai_live_qa --photo-only >"$report_dir/live-photo.log" 2>&1 || live_result=$?
    cat "$report_dir/live-photo.log"
elif [ ! -f "$report_dir/live-photo.log" ]; then
    printf '%s\n' 'RouterAI photo · SKIP · server configuration unavailable' >"$report_dir/live-photo.log"
    cat "$report_dir/live-photo.log"
fi
[ "$browser_result" -eq 0 ] && [ "$live_result" -eq 0 ]
