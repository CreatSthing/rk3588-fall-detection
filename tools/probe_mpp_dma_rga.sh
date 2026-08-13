#!/bin/sh
set -eu

if [ "$#" -lt 2 ] || [ "$#" -gt 5 ]; then
    echo "Usage: $0 <video.mp4|annexb.h264|annexb.h265> <h264|h265> [target_width=640] [target_height=640] [work_dir=/tmp/mpp-dma-rga]" >&2
    exit 2
fi

input=$1
codec=$2
target_width=${3:-640}
target_height=${4:-640}
work_dir=${5:-/tmp/mpp-dma-rga}
probe_app=${MPP_DMA_RGA_PROBE_APP:-/opt/rk3588-camera/current/bin/mpp_dma_rga_probe}

mkdir -p "$work_dir"

case "$input" in
    *.mp4|*.mov|*.mkv)
        annexb="$work_dir/input.annexb.$codec"
        if [ "$codec" = "h264" ]; then
            bsf=h264_mp4toannexb
        elif [ "$codec" = "h265" ]; then
            bsf=hevc_mp4toannexb
        else
            echo "codec must be h264 or h265" >&2
            exit 2
        fi
        ffmpeg -hide_banner -loglevel error -y -i "$input" -an -c:v copy -bsf:v "$bsf" "$annexb"
        ;;
    *)
        annexb=$input
        ;;
esac

"$probe_app" "$annexb" "$codec" "$target_width" "$target_height"
