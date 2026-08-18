"""Build a reproducible mixed calibration set for YOLOv8-Pose RKNN."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}


def images_under(root: Path) -> List[Path]:
    return sorted(path for path in root.rglob("*") if path.suffix.lower() in IMAGE_SUFFIXES)


def has_person_label(image: Path, image_root: Path, label_root: Path) -> bool:
    relative = image.relative_to(image_root).with_suffix(".txt")
    label = label_root / relative
    if not label.is_file():
        return False
    return any(line.split(maxsplit=1)[0] == "0" for line in label.read_text(encoding="utf-8").splitlines() if line.strip())


def load_local_audit(path: Optional[Path]) -> Dict[str, bool]:
    if path is None:
        return {}
    values = json.loads(path.read_text(encoding="utf-8"))
    return {Path(item["path"]).name: bool(item.get("detections")) for item in values}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", type=Path, required=True)
    parser.add_argument("--local-audit", type=Path)
    parser.add_argument("--coco128-root", type=Path, required=True)
    parser.add_argument("--coco8-pose-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--negative-count", type=int, default=15)
    parser.add_argument("--local-negative-count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=3588)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    audit = load_local_audit(args.local_audit)
    local_images = images_under(args.local_root)
    local_positive = [path for path in local_images if audit.get(path.name, True)]
    local_negative = [path for path in local_images if not audit.get(path.name, True)]
    rng.shuffle(local_negative)
    selected: List[Tuple[str, Path]] = [("local-person", path) for path in local_positive]
    selected += [("local-negative", path) for path in local_negative[:args.local_negative_count]]

    coco_images = args.coco128_root / "images"
    coco_labels = args.coco128_root / "labels"
    positives, negatives = [], []
    for path in images_under(coco_images):
        (positives if has_person_label(path, coco_images, coco_labels) else negatives).append(path)
    rng.shuffle(positives)
    rng.shuffle(negatives)
    selected += [("coco128-person", path) for path in positives]
    selected += [("coco128-negative", path) for path in negatives[:args.negative_count]]
    selected += [("coco8-pose", path) for path in images_under(args.coco8_pose_root / "images")]

    image_dir = args.output_root / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    seen = set()
    manifest = []
    for index, (source, path) in enumerate(selected, start=1):
        sha256 = digest(path)
        if sha256 in seen:
            continue
        seen.add(sha256)
        destination = image_dir / f"{index:03d}-{source}-{path.stem}{path.suffix.lower()}"
        shutil.copy2(path, destination)
        manifest.append({"path": destination.relative_to(args.output_root).as_posix(), "source": source, "sha256": sha256})

    dataset = args.output_root / "dataset.txt"
    dataset.write_text("\n".join(item["path"] for item in manifest) + "\n", encoding="utf-8")
    counts: Dict[str, int] = {}
    for item in manifest:
        counts[item["source"]] = counts.get(item["source"], 0) + 1
    report = {"images": len(manifest), "counts": counts, "seed": args.seed, "items": manifest}
    (args.output_root / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"images": len(manifest), "counts": counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
