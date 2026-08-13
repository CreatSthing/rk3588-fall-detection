#!/bin/sh
set -eu

app_root=/opt/rk3588-camera/current
model_path=${MODEL_PATH:-$app_root/share/weights/yolov5s_raw_heads_int8.rknn}
test_image=$app_root/share/media/000057.jpg
result_file=/tmp/rk3588-camera-health-result.jpg
max_temp=${MAX_SOC_TEMP_MILLIC:-85000}

if [ ! -r "$app_root/SHA256SUMS.critical" ]; then
    echo "HEALTHCHECK_FAIL integrity_manifest=missing" >&2
    exit 19
fi
if ! (cd "$app_root" && sha256sum -c SHA256SUMS.critical --quiet); then
    echo "HEALTHCHECK_FAIL integrity=checksum_mismatch" >&2
    exit 19
fi

for required in "$app_root/bin/yolov5_img" "$model_path" "$test_image"; do
    if [ ! -r "$required" ]; then
        echo "HEALTHCHECK_FAIL missing=$required" >&2
        exit 20
    fi
done

soc_temp=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
if [ "$soc_temp" -gt "$max_temp" ]; then
    echo "HEALTHCHECK_FAIL soc_temp_millic=$soc_temp limit=$max_temp" >&2
    exit 21
fi

rm -f "$result_file"
if ! "$app_root/bin/yolov5_img" "$model_path" "$test_image" "$result_file"; then
    echo "HEALTHCHECK_FAIL inference=failed" >&2
    exit 22
fi
if [ ! -s "$result_file" ]; then
    echo "HEALTHCHECK_FAIL output=empty" >&2
    exit 23
fi
rm -f "$result_file"
echo "HEALTHCHECK_OK soc_temp_millic=$soc_temp"
