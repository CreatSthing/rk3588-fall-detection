#!/usr/bin/env bash
set -u

MODEL=${MODEL:-/opt/rk3588-camera/current/assets/weights/yolov5s_raw_heads_int8.rknn}
MP4=${MP4:-/opt/rk3588-camera/current/assets/media/c3_1080.mp4}
ANNEXB=${ANNEXB:-/tmp/c3_1080.annexb.h264}
APP=${APP:-/opt/rk3588-camera/current/bin/mpp_rga_thread_pool}
MAX_FRAMES=${MAX_FRAMES:-120}

if [ ! -f "$ANNEXB" ]; then
  ffmpeg -hide_banner -loglevel error -y \
    -i "$MP4" \
    -an -c:v copy -bsf:v h264_mp4toannexb \
    "$ANNEXB"
fi

printf 'contexts,fps,submitted,completed,decode_errors,inference_errors,avg_decode_rga_ms,rc\n'
for contexts in $(seq 1 16); do
  log="/tmp/mpp_context_${contexts}.log"
  "$APP" "$MODEL" "$ANNEXB" h264 "$contexts" 0 "$MAX_FRAMES" >"$log" 2>&1
  rc=$?
  line=$(grep 'MPP_RGA_POOL_OK' "$log" | tail -n 1 || true)
  if [ -z "$line" ]; then
    printf '%s,NA,NA,NA,NA,NA,NA,%s\n' "$contexts" "$rc"
    tail -n 8 "$log" >&2
    continue
  fi
  fps=$(printf '%s\n' "$line" | sed -n 's/.* fps=\([0-9.]*\).*/\1/p')
  submitted=$(printf '%s\n' "$line" | sed -n 's/.* submitted=\([0-9]*\).*/\1/p')
  completed=$(printf '%s\n' "$line" | sed -n 's/.* completed=\([0-9]*\).*/\1/p')
  decode_errors=$(printf '%s\n' "$line" | sed -n 's/.* decode_errors=\([0-9]*\).*/\1/p')
  inference_errors=$(printf '%s\n' "$line" | sed -n 's/.* inference_errors=\([0-9]*\).*/\1/p')
  avg_decode_rga_ms=$(printf '%s\n' "$line" | sed -n 's/.* avg_decode_rga_ms=\([0-9.]*\).*/\1/p')
  printf '%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$contexts" "$fps" "$submitted" "$completed" "$decode_errors" \
    "$inference_errors" "$avg_decode_rga_ms" "$rc"
done
