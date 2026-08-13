#!/usr/bin/env bash
set -u

MODEL=${MODEL:-/opt/rk3588-camera/current/assets/weights/yolov5s_raw_heads_int8.rknn}
VIDEO=${VIDEO:-/opt/rk3588-camera/current/assets/media/c3_1080.mp4}
APP=${APP:-/opt/rk3588-camera/current/bin/yolov5_thread_pool}

printf 'contexts,fps,submitted,completed,errors,queue_avg_ms,npu_avg_ms,rc\n'
for contexts in $(seq 1 16); do
  log="/tmp/yolov5_context_${contexts}.log"
  profile="/tmp/yolov5_context_${contexts}.frames.csv"
  "$APP" "$MODEL" "$VIDEO" "$contexts" 0 software "$profile" >"$log" 2>&1
  rc=$?
  ok=$(grep 'POOL_OK' "$log" | tail -n 1 || true)
  queue=$(grep 'PROFILE_STAGE queue' "$log" | tail -n 1 || true)
  npu=$(grep 'PROFILE_STAGE npu' "$log" | tail -n 1 || true)
  if [ -z "$ok" ]; then
    printf '%s,NA,NA,NA,NA,NA,NA,%s\n' "$contexts" "$rc"
    tail -n 8 "$log" >&2
    continue
  fi
  fps=$(printf '%s\n' "$ok" | sed -n 's/.* fps=\([0-9.]*\).*/\1/p')
  submitted=$(printf '%s\n' "$ok" | sed -n 's/.* submitted=\([0-9]*\).*/\1/p')
  completed=$(printf '%s\n' "$ok" | sed -n 's/.* completed=\([0-9]*\).*/\1/p')
  errors=$(printf '%s\n' "$ok" | sed -n 's/.* errors=\([0-9]*\).*/\1/p')
  queue_avg=$(printf '%s\n' "$queue" | sed -n 's/.* avg=\([0-9.]*\).*/\1/p')
  npu_avg=$(printf '%s\n' "$npu" | sed -n 's/.* avg=\([0-9.]*\).*/\1/p')
  printf '%s,%s,%s,%s,%s,%s,%s,%s\n' \
    "$contexts" "$fps" "$submitted" "$completed" "$errors" \
    "$queue_avg" "$npu_avg" "$rc"
done
