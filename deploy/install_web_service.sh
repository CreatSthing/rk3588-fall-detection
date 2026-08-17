#!/bin/sh
set -eu

app_root=${1:-/opt/rk3588-camera/current}
etc_dir=${RK3588_ETC_DIR:-/etc/rk3588-camera}
service_user=${RK3588_SERVICE_USER:-rkcamera}
service_group=${RK3588_SERVICE_GROUP:-rkcamera}
service_file=/etc/systemd/system/rk3588-web.service

if [ "$(id -u)" -ne 0 ]; then
    echo "Please run as root: sudo $0 [$app_root]" >&2
    exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is required" >&2
    exit 3
fi

if ! id "$service_user" >/dev/null 2>&1; then
    useradd --system --create-home --shell /usr/sbin/nologin "$service_user"
fi

for group_name in video render; do
    if getent group "$group_name" >/dev/null 2>&1; then
        usermod -aG "$group_name" "$service_user"
    fi
done

install -d -m 0755 "$etc_dir" /var/lib/rk3588-camera /var/lib/rk3588-camera/events /var/log/rk3588-camera
chown -R "$service_user:$service_group" /var/lib/rk3588-camera /var/log/rk3588-camera

if [ ! -f "$etc_dir/web.env" ]; then
    install -m 0644 "$app_root/deploy/rk3588-web.env.example" "$etc_dir/web.env"
fi
if ! grep -q '^RK3588_EVENT_DIR=' "$etc_dir/web.env"; then
    printf '%s\n' 'RK3588_EVENT_DIR=/var/lib/rk3588-camera/events' >> "$etc_dir/web.env"
fi
if ! grep -q '^RK3588_EVENT_DB=' "$etc_dir/web.env"; then
    printf '%s\n' 'RK3588_EVENT_DB=/var/lib/rk3588-camera/events/events.db' >> "$etc_dir/web.env"
fi

if [ ! -f "$etc_dir/web.json" ]; then
    install -m 0644 "$app_root/apps/web/backend/config.example.json" "$etc_dir/web.json"
fi

python3 -m venv --system-site-packages "$app_root/.venv"
pip_index=${RK3588_PIP_INDEX_URL:-https://repo.huaweicloud.com/repository/pypi/simple}
pip_trusted_host=${RK3588_PIP_TRUSTED_HOST:-repo.huaweicloud.com}
"$app_root/.venv/bin/python" -m pip install \
    -i "$pip_index" \
    --trusted-host "$pip_trusted_host" \
    -r "$app_root/apps/web/backend/requirements.txt" \
    -r "$app_root/apps/fall_detection/requirements.txt"
if ! "$app_root/.venv/bin/python" -c 'import cv2, numpy; from rknnlite.api import RKNNLite' >/dev/null 2>&1; then
    echo "OpenCV or rknn-toolkit-lite2 is missing. Install the board/Runtime-matched packages before deployment." >&2
    exit 4
fi
chmod +x "$app_root/deploy/run_web.sh"
chown -R "$service_user:$service_group" "$app_root/.venv"

install -m 0644 "$app_root/deploy/rk3588-web.service" "$service_file"
systemctl daemon-reload
systemctl enable rk3588-web.service
systemctl restart rk3588-web.service

echo "RK3588 web console service installed."
echo "Status: systemctl status rk3588-web --no-pager"
echo "Logs:   journalctl -u rk3588-web -f"
