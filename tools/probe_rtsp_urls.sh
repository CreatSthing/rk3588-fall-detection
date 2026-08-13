#!/usr/bin/env bash
set -u

host=${1:?usage: probe_rtsp_urls.sh <host> <user> <password>}
user=${2:?usage: probe_rtsp_urls.sh <host> <user> <password>}
password=${3:?usage: probe_rtsp_urls.sh <host> <user> <password>}

paths=(
  "stream1"
  "stream2"
  "live/ch00_0"
  "live/ch00_1"
  "h264/ch1/main/av_stream"
  "h264/ch1/sub/av_stream"
  "Streaming/Channels/101"
  "Streaming/Channels/102"
  "cam/realmonitor?channel=1&subtype=0"
  "cam/realmonitor?channel=1&subtype=1"
)

for path in "${paths[@]}"; do
  url="rtsp://${user}:${password}@${host}:554/${path}"
  echo "TRY rtsp://${user}:***@${host}:554/${path}"
  if timeout 8 ffprobe -v error -rtsp_transport tcp \
      -select_streams v:0 -show_entries stream=codec_name,width,height,avg_frame_rate \
      -of default=nw=1 "$url"; then
    echo "FOUND rtsp://${user}:***@${host}:554/${path}"
    exit 0
  fi
done

echo "NO_RTSP_MATCH"
exit 1
