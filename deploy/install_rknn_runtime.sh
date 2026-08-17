#!/bin/sh
set -eu

runtime_source=${1:?usage: install_rknn_runtime.sh <librknnrt.so> [expected-sha256]}
expected_sha=${2:-}
runtime_target=/usr/lib/librknnrt.so

if [ "$(id -u)" -ne 0 ]; then
    echo "Run as root." >&2
    exit 2
fi
if [ ! -s "$runtime_source" ]; then
    echo "Runtime library is missing or empty: $runtime_source" >&2
    exit 3
fi
actual_sha=$(sha256sum "$runtime_source" | awk '{print $1}')
if [ -n "$expected_sha" ] && [ "$actual_sha" != "$expected_sha" ]; then
    echo "Runtime checksum mismatch: $actual_sha" >&2
    exit 4
fi
if [ -f "$runtime_target" ] && cmp -s "$runtime_source" "$runtime_target"; then
    echo "RKNN Runtime is already installed: $actual_sha"
    exit 0
fi

backup_path="$runtime_target.backup-$(date +%Y%m%d-%H%M%S)"
if [ -f "$runtime_target" ]; then
    cp -a "$runtime_target" "$backup_path"
    echo "Previous Runtime backed up to $backup_path"
fi
install -m 0755 "$runtime_source" "$runtime_target"
ldconfig
echo "Installed RKNN Runtime: $actual_sha"
strings "$runtime_target" | grep 'librknnrt version' | head -n 1 || true
