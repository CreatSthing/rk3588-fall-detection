#!/bin/sh
set -eu

app_root=${RK3588_APP_ROOT:-/opt/rk3588-camera/current}
host=${WEB_HOST:-0.0.0.0}
port=${WEB_PORT:-8000}

export LD_LIBRARY_PATH="$app_root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

cd "$app_root"
exec "$app_root/.venv/bin/python" -m uvicorn apps.web.backend.app:app \
    --host "$host" \
    --port "$port"
