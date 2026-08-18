FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RK3588_APP_ROOT=/app \
    RK3588_WEB_CONFIG=/var/lib/rk3588-camera/web.json \
    RK3588_EVENT_DIR=/var/lib/rk3588-camera/events \
    RK3588_EVENT_DB=/var/lib/rk3588-camera/events/events.db \
    RK3588_MANUAL_RECORD_DIR=/var/lib/rk3588-camera/recordings

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        python3 \
        python3-numpy \
        python3-opencv \
        python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY apps/web/backend/requirements.txt /tmp/web-requirements.txt
COPY apps/fall_detection/requirements.txt /tmp/fall-requirements.txt
COPY vendor/ /tmp/vendor/

RUN python3 -m pip install --no-cache-dir --upgrade "pip<25" setuptools wheel

RUN numpy_wheel="$(find /tmp/vendor -maxdepth 1 -type f -name 'numpy-*aarch64.whl' | sort | tail -n 1)" \
    && test -n "$numpy_wheel" \
    && python3 -m pip install --no-cache-dir "$numpy_wheel" \
    && python3 -m pip install --no-cache-dir \
        -r /tmp/web-requirements.txt \
        -r /tmp/fall-requirements.txt \
    && wheel_path="$(find /tmp/vendor -maxdepth 1 -type f -name 'rknn_toolkit_lite2-*.whl' | sort | tail -n 1)" \
    && test -n "$wheel_path" \
    && python3 -m pip install --no-cache-dir "$wheel_path" \
    && rm -rf /tmp/vendor

COPY . /app

RUN sed -i 's/\r$//' \
        /app/deploy/docker-entrypoint.sh \
        /app/deploy/run_gst_mpp_stream.sh \
    && chmod +x \
        /app/deploy/docker-entrypoint.sh \
        /app/deploy/run_gst_mpp_stream.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/status', timeout=3)" || exit 1

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
