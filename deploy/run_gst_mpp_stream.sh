#!/bin/sh
set -eu

# Push a local H.264 MP4 file or RTSP H.264 camera stream to MediaMTX.
#
# Local MP4: why not "-c:v copy"?
#   The sample MP4 contains B-frames. MediaMTX WebRTC rejects H.264 streams with
#   B-frames. Re-encoding with mpph264enc produces a WebRTC-friendly stream while
#   moving the heavy encoding work from CPU/libx264 to RK3588 VPU/MPP.
#
# RTSP camera: default to "-c:v copy".
#   Many IPC sub-streams are already low-resolution H.264 with no B-frames. In
#   that case re-encoding is wasted work and may add latency. Set RTSP_REENCODE=1
#   only when the camera stream is not WebRTC-friendly.
#
# The board image used by this project does not ship GStreamer's rtspclientsink,
# so FFmpeg is used twice for light-weight container work:
#   1) input FFmpeg loops/demuxes the MP4 and copies H.264 packets to stdout;
#   2) GStreamer decodes + re-encodes with Rockchip MPP/VPU;
#   3) output FFmpeg copies the encoded packets into RTSP without re-encoding.

input=${1:-/opt/rk3588-camera/current/assets/media/c3_1080.mp4}
rtsp_url=${2:-rtsp://127.0.0.1:8554/live/raw}
bitrate=${GST_MPP_BITRATE:-4000000}
gop=${GST_MPP_GOP:-25}
rtsp_reencode=${RTSP_REENCODE:-0}

child_pid=""

stop_child() {
    if [ -n "$child_pid" ] && kill -0 "$child_pid" 2>/dev/null; then
        kill "$child_pid" 2>/dev/null || true
        wait "$child_pid" 2>/dev/null || true
    fi
}

trap 'stop_child; exit 0' INT TERM

while true; do
    if echo "$input" | grep -Eq '^rtsp://'; then
        if [ "$rtsp_reencode" = "1" ]; then
            ffmpeg -hide_banner -loglevel warning \
                -rtsp_transport tcp \
                -i "$input" \
                -an -c:v copy \
                -f h264 pipe:1 \
                | gst-launch-1.0 -q -e \
                fdsrc fd=0 ! h264parse ! mppvideodec ! \
                videoconvert ! video/x-raw,format=NV12 ! \
                mpph264enc bps="$bitrate" gop="$gop" profile=baseline header-mode=each-idr ! \
                h264parse config-interval=-1 ! \
                video/x-h264,stream-format=byte-stream,alignment=au ! \
                fdsink fd=1 sync=true \
                | ffmpeg -hide_banner -loglevel warning \
                    -fflags nobuffer \
                    -f h264 -i pipe:0 \
                    -an -c:v copy \
                    -f rtsp "$rtsp_url" \
                    &
        else
            ffmpeg -hide_banner -loglevel warning \
                -rtsp_transport tcp \
                -i "$input" \
                -an -c:v copy \
                -f rtsp "$rtsp_url" \
                &
        fi
    else
        ffmpeg -hide_banner -loglevel warning \
            -re -stream_loop -1 \
            -i "$input" \
            -an -c:v copy -bsf:v h264_mp4toannexb \
            -f h264 pipe:1 \
            | gst-launch-1.0 -q -e \
            fdsrc fd=0 ! h264parse ! mppvideodec ! \
            videoconvert ! video/x-raw,format=NV12 ! \
            mpph264enc bps="$bitrate" gop="$gop" profile=baseline header-mode=each-idr ! \
            h264parse config-interval=-1 ! \
            video/x-h264,stream-format=byte-stream,alignment=au ! \
            fdsink fd=1 sync=true \
            | ffmpeg -hide_banner -loglevel warning \
                -fflags nobuffer \
                -f h264 -i pipe:0 \
                -an -c:v copy \
                -f rtsp "$rtsp_url" \
                &
    fi
    child_pid=$!
    wait "$child_pid" || true
    child_pid=""
    sleep 0.2
done
