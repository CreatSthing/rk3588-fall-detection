#!/usr/bin/env bash
set -u

MODEL=${MODEL:-/opt/rk3588-camera/current/assets/weights/yolov5s_raw_heads_int8.rknn}
INPUT=${INPUT:-/tmp/c3_1080.fixcheck.annexb.h264}
CODEC=${CODEC:-h264}
APP=${APP:-/opt/rk3588-camera/current/bin/mpp_rga_thread_pool}
MAX_FRAMES=${MAX_FRAMES:-120}
CONTEXTS=${CONTEXTS:-"1 2 3 4 5 6 7 8"}

echo "contexts,fps,submitted,completed,decode_errors,inference_errors,rc"
for c in $CONTEXTS; do
  log="/tmp/mpp_fix2_c${c}.log"
  "$APP" "$MODEL" "$INPUT" "$CODEC" "$c" 0 "$MAX_FRAMES" >"$log" 2>&1
  rc=$?
  python3 - "$c" "$rc" "$log" <<'PY'
import re, sys
c, rc, log = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(log, errors="ignore").read()
m = re.search(r"MPP_RGA_POOL_OK .*", text)
if not m:
    print(f"{c},NA,NA,NA,NA,NA,{rc}")
    sys.exit(0)
line = m.group(0)
def get(name):
    mm = re.search(name + r"=([0-9.]+)", line)
    return mm.group(1) if mm else ""
print(",".join([
    c, get("fps"), get("submitted"), get("completed"),
    get("decode_errors"), get("inference_errors"), rc
]))
PY
done
