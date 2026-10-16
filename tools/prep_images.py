"""Нормализация готовых карточек витрины: raw/*.jpg → demo/images/*.jpg.

Фон и композицию готовит внешний инструмент. Скрипт меняет только геометрию
(кроп по центру в квадрат, ресайз) и вес файла (JPEG, без EXIF).
Пиксели внутри кадра не перекрашиваются: цветовой профиль сохраняется как был.

    python tools/prep_images.py [--src raw] [--dst demo/images]
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = PROJECT_ROOT / 'raw'
DEFAULT_DST = PROJECT_ROOT / 'demo' / 'images'

SIZE = 800
QUALITY = 80
MIN_QUALITY = 60
QUALITY_STEP = 5
MAX_BYTES = 150 * 1024


@dataclass(frozen=True)
class Result:
    name: str
    source_size: tuple[int, int]
    quality: int
    bytes: int


def center_square(image: Image.Image) -> Image.Image:
    width, height = image.size
    if width == height:
        return image
    side = min(width, height)
    left = (width - side) // 2
    top = (height - side) // 2
    return image.crop((left, top, left + side, top + side))


def _encode(image: Image.Image, quality: int, icc_profile: bytes | None) -> bytes:
    buffer = io.BytesIO()
    options: dict[str, object] = {'format': 'JPEG', 'quality': quality, 'optimize': True}
    if icc_profile:
        options['icc_profile'] = icc_profile
    # exif не передаётся, поэтому Pillow пишет файл без EXIF-блока.
    image.save(buffer, **options)
    return buffer.getvalue()


def normalize(
    src: Path,
    dst: Path,
    size: int = SIZE,
    quality: int = QUALITY,
    max_bytes: int = MAX_BYTES,
    min_quality: int = MIN_QUALITY,
) -> Result:
    with Image.open(src) as opened:
        source_size = opened.size
        icc_profile = opened.info.get('icc_profile')
        # Поворот по EXIF Orientation применяем до удаления EXIF, иначе кадр ляжет на бок.
        image = ImageOps.exif_transpose(opened)
        if image.mode != 'RGB':
            image = image.convert('RGB')
        image = center_square(image).resize((size, size), Image.Resampling.LANCZOS)

    current = quality
    payload = _encode(image, current, icc_profile)
    while len(payload) > max_bytes and current - QUALITY_STEP >= min_quality:
        current -= QUALITY_STEP
        payload = _encode(image, current, icc_profile)
    if len(payload) > max_bytes:
        raise ValueError(
            f'{src.name}: {len(payload) // 1024} КБ при качестве {current}, лимит {max_bytes // 1024} КБ'
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f'.{dst.name}.tmp')
    tmp.write_bytes(payload)
    tmp.replace(dst)
    return Result(name=dst.name, source_size=source_size, quality=current, bytes=len(payload))


def source_files(src_dir: Path) -> list[Path]:
    return sorted(p for p in src_dir.iterdir() if p.is_file() and p.suffix.lower() in {'.jpg', '.jpeg'})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Квадрат 800×800, JPEG ≤150 КБ, без EXIF.')
    parser.add_argument('--src', type=Path, default=DEFAULT_SRC)
    parser.add_argument('--dst', type=Path, default=DEFAULT_DST)
    args = parser.parse_args(argv)

    if not args.src.is_dir():
        print(f'Нет каталога {args.src}', file=sys.stderr)
        return 1
    files = source_files(args.src)
    if not files:
        print(f'В {args.src} нет jpg', file=sys.stderr)
        return 1

    failed = 0
    for path in files:
        try:
            result = normalize(path, args.dst / path.name)
        except Exception as error:
            failed += 1
            print(f'FAIL {path.name}: {error}', file=sys.stderr)
            continue
        width, height = result.source_size
        print(f'OK   {result.name}: {width}x{height} → {SIZE}x{SIZE}, q={result.quality}, {result.bytes / 1024:.1f} КБ')
    print(f'Готово: {len(files) - failed}/{len(files)} → {args.dst}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
