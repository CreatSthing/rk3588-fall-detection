#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
build_dir=${BUILD_DIR:-"$repo_root/build-rk3588-vm"}
install_dir=${INSTALL_DIR:-"$repo_root/output/rk3588-vm-package"}
opencv_dir=${OpenCV_DIR:-"$repo_root/3rdparty/opencv/opencv-linux-aarch64/share/OpenCV"}
use_bundled_opencv=${USE_BUNDLED_OPENCV:-0}

opencv_args=()
if [ "$use_bundled_opencv" = "1" ]; then
  opencv_args+=("-DOpenCV_DIR=$opencv_dir")
fi

cmake -S "$repo_root" -B "$build_dir" \
  -DCMAKE_TOOLCHAIN_FILE="$repo_root/cmake/aarch64-linux-gnu.toolchain.cmake" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$install_dir" \
  "${opencv_args[@]}" \
  -DUSE_MANUAL_OPENCV=ON \
  -DUSE_SYSTEM_RGA=OFF \
  -DBUILD_RK3588_TARGETS=ON \
  -DBUILD_STREAM_TARGETS=ON

cmake --build "$build_dir" -j"$(nproc)"
cmake --install "$build_dir"

echo "RK3588 package installed to: $install_dir"
