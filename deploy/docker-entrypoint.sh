#!/bin/sh
set -eu

app_root=${RK3588_APP_ROOT:-/app}
config_path=${RK3588_WEB_CONFIG:-/var/lib/rk3588-camera/web.json}
data_dir=$(dirname "$config_path")
event_dir=${RK3588_EVENT_DIR:-/var/lib/rk3588-camera/events}
manual_dir=${RK3588_MANUAL_RECORD_DIR:-/var/lib/rk3588-camera/recordings}
model_path=${RK3588_MODEL_PATH:-$app_root/assets/weights/yolov8n-pose-int8-calibrated-20260818.rknn}

mkdir -p "$data_dir" "$event_dir" "$manual_dir"

if [ ! -f "$config_path" ]; then
    sed "s#/opt/rk3588-camera/current#$app_root#g" \
        "$app_root/apps/web/backend/config.example.json" > "$config_path"
fi
python3 "$app_root/deploy/rewrite_config_paths.py" docker "$config_path"

if [ ! -s "$model_path" ]; then
    echo "Missing RKNN model: $model_path" >&2
    exit 4
fi
if [ ! -r /usr/lib/librknnrt.so ]; then
    echo "Missing mounted RKNN Runtime: /usr/lib/librknnrt.so" >&2
    exit 5
fi
if [ ! -d /dev/dri ]; then
    echo "Missing mounted RK3588 device directory: /dev/dri" >&2
    exit 6
fi

python3 -c 'import cv2, numpy; from rknnlite.api import RKNNLite'

exec python3 -m uvicorn apps.web.backend.app:app \
    --host "${WEB_HOST:-0.0.0.0}" \
    --port "${WEB_PORT:-8000}"
