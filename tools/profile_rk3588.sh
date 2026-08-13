#!/bin/sh
set -eu

if [ "$#" -lt 3 ] || [ "$#" -gt 6 ]; then
    echo "Usage: $0 <model.rknn> <video> <output-prefix> [contexts=3] [draw=1] [decoder=software]" >&2
    exit 2
fi

model=$1
video=$2
prefix=$3
contexts=${4:-3}
draw=${5:-1}
decoder=${6:-software}
app=${PROFILE_APP:-/opt/rk3588-camera/current/bin/yolov5_thread_pool}

mkdir -p "$(dirname "$prefix")"

sampler_pids=""
stop_samplers()
{
    for sampler_pid in $sampler_pids; do
        kill "$sampler_pid" 2>/dev/null || true
        wait "$sampler_pid" 2>/dev/null || true
    done
}
trap stop_samplers EXIT INT TERM

"$app" "$model" "$video" "$contexts" "$draw" "$decoder" "$prefix.frames.csv" \
    >"$prefix.app.log" 2>&1 &
app_pid=$!

if command -v pidstat >/dev/null 2>&1; then
    pidstat -h -t -u -r -p "$app_pid" 1 >"$prefix.pidstat.log" 2>&1 &
    sampler_pids="$sampler_pids $!"
fi
if command -v mpstat >/dev/null 2>&1; then
    mpstat -P ALL 1 >"$prefix.mpstat.log" 2>&1 &
    sampler_pids="$sampler_pids $!"
fi

set +e
wait "$app_pid"
app_status=$?
set -e
stop_samplers
sampler_pids=""

grep -E 'Completed:|PROFILE |PROFILE_' "$prefix.app.log" || true
exit "$app_status"
