#!/usr/bin/env python3
"""Rewrite shared web config paths for Docker or systemd deployment."""

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable, Set


def strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)


def rewrite(value: Any, mode: str, app_root: str, systemd_roots: Set[str]) -> Any:
    if isinstance(value, str):
        if mode == "docker":
            if value.endswith("/.venv/bin/python"):
                return "python3"
            for root in sorted(systemd_roots, key=len, reverse=True):
                if value == root or value.startswith(root + "/"):
                    return "/app" + value[len(root):]
        else:
            if value == "python3":
                return app_root + "/.venv/bin/python"
            if value == "/app" or value.startswith("/app/"):
                return app_root + value[len("/app"):]
        return value
    if isinstance(value, list):
        return [rewrite(item, mode, app_root, systemd_roots) for item in value]
    if isinstance(value, dict):
        return {key: rewrite(item, mode, app_root, systemd_roots) for key, item in value.items()}
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("docker", "systemd"))
    parser.add_argument("config", type=Path)
    parser.add_argument("app_root", nargs="?", default="/opt/rk3588-camera/current")
    args = parser.parse_args()

    if not args.config.exists():
        return 0
    original_stat = args.config.stat()
    with args.config.open(encoding="utf-8") as stream:
        config = json.load(stream)

    roots = {args.app_root.rstrip("/"), "/opt/rk3588-camera/current"}
    for value in strings(config):
        suffix = "/.venv/bin/python"
        if value.endswith(suffix):
            roots.add(value[:-len(suffix)])

    updated = rewrite(config, args.mode, args.app_root.rstrip("/"), roots)
    if updated == config:
        return 0

    temporary = args.config.with_suffix(args.config.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(updated, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temporary, original_stat.st_mode)
    if hasattr(os, "chown"):
        os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
    temporary.replace(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
