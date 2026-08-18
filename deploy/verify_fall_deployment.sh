#!/bin/sh
set -eu

app_root=${1:-/opt/rk3588-camera/current}
source=${2:-rtsp://127.0.0.1:8554/live/cam1}
frames=${3:-30}
model="$app_root/assets/weights/yolov8n-pose-int8-calibrated-20260818.rknn"
work_dir=$(mktemp -d /tmp/rk3588-fall-verify.XXXXXX)
trap 'rm -rf "$work_dir"' EXIT INT TERM

test -x "$app_root/.venv/bin/python"
test -s "$model"
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null

export LD_LIBRARY_PATH="$app_root/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
"$app_root/.venv/bin/python" -c 'import cv2, numpy; from rknnlite.api import RKNNLite; print("Python/RKNN imports: OK")'

cd "$app_root"
"$app_root/.venv/bin/python" -m apps.fall_detection.main \
    --model "$model" \
    --source "$source" \
    --camera-id verify \
    --event-dir "$work_dir/events" \
    --decoder auto \
    --max-frames "$frames" \
    > "$work_dir/output.jsonl" \
    2> "$work_dir/stderr.log"

"$app_root/.venv/bin/python" - "$work_dir/output.jsonl" "$frames" <<'PY'
import json
import sys

path, expected_text = sys.argv[1:]
payloads = []
with open(path, encoding="utf-8") as stream:
    for line in stream:
        if line.startswith("{"):
            payloads.append(json.loads(line))
expected = int(expected_text)
if len(payloads) != expected:
    raise SystemExit(f"Expected {expected} frames, received {len(payloads)}")
last = payloads[-1]
print(f"Frames: {len(payloads)}")
print(f"Pipeline FPS: {last.get('fps')}")
print(f"Last-frame detections: {len(last.get('detections') or [])}")
PY

if grep -Eq 'RgaBlit|Invalid RKNN model|Traceback' "$work_dir/stderr.log"; then
    echo "Critical errors found:" >&2
    grep -E 'RgaBlit|Invalid RKNN model|Traceback' "$work_dir/stderr.log" >&2
    exit 5
fi
echo "Fall pipeline verification: OK"
