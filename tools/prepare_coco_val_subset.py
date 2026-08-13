#!/usr/bin/env python3
"""Prepare a reproducible, person-focused subset of COCO val2017."""

import argparse
import random
import shutil
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CALIBRATION_ROOT = PROJECT_ROOT / "assets" / "calibration"
LABELS_URL = "https://github.com/ultralytics/assets/releases/download/v0.0.0/coco2017labels.zip"
IMAGE_URL = "http://images.cocodataset.org/val2017/{file_name}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a stratified COCO val2017 subset for quantization checks.")
    parser.add_argument("--count", type=int, default=500, help="Total images; half contain person annotations.")
    parser.add_argument("--seed", type=int, default=20260811)
    parser.add_argument("--output-root", type=Path, default=CALIBRATION_ROOT / "coco_val500")
    parser.add_argument("--list-output", type=Path, default=CALIBRATION_ROOT / "coco_val500.txt")
    parser.add_argument("--workers", type=int, default=8)
    return parser.parse_args()


def download(url: str, destination: Path) -> None:
    if destination.is_file() and destination.stat().st_size > 0:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(5):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output)
            temporary.replace(destination)
            return
        except Exception:
            if temporary.exists():
                temporary.unlink()
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def has_person(label_path: Path) -> bool:
    if not label_path.is_file():
        return False
    return any(line.split(maxsplit=1)[0] == "0" for line in label_path.read_text(encoding="utf-8").splitlines() if line)


def main() -> int:
    args = parse_args()
    if args.count < 2 or args.count % 2:
        raise ValueError("--count must be an even number greater than one")

    labels_zip = CALIBRATION_ROOT / "coco2017labels.zip"
    extracted_root = CALIBRATION_ROOT / "coco2017"
    source_labels = extracted_root / "coco" / "labels" / "val2017"
    source_list = extracted_root / "coco" / "val2017.txt"
    download(LABELS_URL, labels_zip)
    if not source_list.is_file():
        with zipfile.ZipFile(labels_zip) as archive:
            archive.extract("coco/val2017.txt", extracted_root)
            for name in archive.namelist():
                if name.startswith("coco/labels/val2017/"):
                    archive.extract(name, extracted_root)

    file_names = [Path(line.strip()).name for line in source_list.read_text(encoding="utf-8").splitlines() if line.strip()]
    calibration_ids = {
        Path(line.strip()).stem
        for line in (CALIBRATION_ROOT / "coco128_calibration.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    file_names = [file_name for file_name in file_names if Path(file_name).stem not in calibration_ids]
    person_images = [file_name for file_name in file_names if has_person(source_labels / f"{Path(file_name).stem}.txt")]
    background_images = [file_name for file_name in file_names if file_name not in set(person_images)]

    rng = random.Random(args.seed)
    half = args.count // 2
    selected = rng.sample(person_images, half) + rng.sample(background_images, half)
    rng.shuffle(selected)

    output_root = args.output_root.resolve()
    list_output = args.list_output.resolve()
    image_dir = output_root / "images" / "val2017"
    label_dir = output_root / "labels" / "val2017"
    image_dir.mkdir(parents=True, exist_ok=True)
    label_dir.mkdir(parents=True, exist_ok=True)

    def fetch(file_name: str) -> None:
        download(IMAGE_URL.format(file_name=file_name), image_dir / file_name)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        list(executor.map(fetch, selected))

    for file_name in selected:
        source = source_labels / f"{Path(file_name).stem}.txt"
        destination = label_dir / source.name
        if source.is_file():
            shutil.copyfile(source, destination)
        else:
            destination.write_text("", encoding="utf-8")

    entries = [(image_dir / file_name).relative_to(list_output.parent.resolve()).as_posix() for file_name in selected]
    list_output.write_text("\n".join(entries) + "\n", encoding="utf-8")
    person_boxes = sum(
        line.split(maxsplit=1)[0] == "0"
        for file_name in selected
        for line in (label_dir / f"{Path(file_name).stem}.txt").read_text(encoding="utf-8").splitlines()
        if line
    )
    print(f"images: {len(selected)} (person={half}, background={half})")
    print(f"person boxes: {person_boxes}")
    print(f"list: {list_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
