#!/usr/bin/env python3
"""Capture one synchronized preview/detection pair from the WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
from pathlib import Path

import websockets


async def capture(url: str, timeout: float) -> dict:
    async with websockets.connect(url, max_size=4 * 1024 * 1024) as socket:
        await socket.send("pose preview verification client ready")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no synchronized pose preview received within {timeout:g} seconds")
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
            payload = message.get("payload") or {}
            if message.get("type") == "detection" and payload.get("preview_jpeg") and payload.get("detections"):
                return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/detections")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = asyncio.run(capture(args.url, args.timeout))
    jpeg = base64.b64decode(payload.pop("preview_jpeg"))
    (output_dir / "preview.jpg").write_bytes(jpeg)
    (output_dir / "detection.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"frame_id": payload["frame_id"], "jpeg_bytes": len(jpeg), "detections": len(payload["detections"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
