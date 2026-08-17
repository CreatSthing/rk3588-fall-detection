#!/usr/bin/env python3
"""Wait for one real-time alarm message and print it as JSON."""

from __future__ import annotations

import argparse
import asyncio
import json

import websockets


async def wait_for_alarm(url: str, timeout: float) -> dict:
    async with websockets.connect(url) as socket:
        await socket.send("alarm verification client ready")
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"no alarm received within {timeout:g} seconds")
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining))
            if message.get("type") == "alarm":
                return message


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8000/ws/detections")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(wait_for_alarm(args.url, args.timeout)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
