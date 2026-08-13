#!/usr/bin/env python3
import argparse
from pathlib import Path


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Create an RKNN quantization dataset file from image folders.")
    parser.add_argument("--image-dir", action="append", required=True, help="Image folder. Can be passed multiple times.")
    parser.add_argument("--output", required=True, help="Output dataset txt path.")
    parser.add_argument(
        "--relative-to",
        default=None,
        help="Write paths relative to this directory. Defaults to the output file directory.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    base = Path(args.relative_to).resolve() if args.relative_to else output.resolve().parent
    images = []
    for image_dir in args.image_dir:
        root = Path(image_dir)
        if not root.exists():
            raise FileNotFoundError(f"image directory not found: {root}")
        images.extend(
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        )

    images = sorted(set(path.resolve() for path in images))
    if not images:
        raise RuntimeError("no calibration images found")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as f:
        for path in images:
            try:
                f.write(path.relative_to(base).as_posix())
            except ValueError:
                f.write(path.as_posix())
            f.write("\n")

    print(f"wrote {len(images)} image paths to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
