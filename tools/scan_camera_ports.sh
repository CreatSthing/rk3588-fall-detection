#!/usr/bin/env bash
set -u

hosts=${1:-"192.168.1.2 192.168.1.4 192.168.1.7 192.168.1.9 192.168.1.15 192.168.1.21"}
ports=${2:-"80 81 88 443 554 8554 8000 8080 8081 8899 5000 3702"}

for host in $hosts; do
  echo "HOST $host"
  for port in $ports; do
    if timeout 1 bash -c ":</dev/tcp/$host/$port" >/dev/null 2>&1; then
      echo "  open:$port"
    fi
  done
done
