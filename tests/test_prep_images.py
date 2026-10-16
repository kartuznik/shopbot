from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from tools.prep_images import MAX_BYTES, SIZE, center_square, main, normalize

RED = (220, 20, 20)
BACKGROUND = (14, 22, 38)  # #0E1626


def _synthetic(path: Path, size: tuple[int, int] = (1200, 900)) -> Path:
    """Неквадратный кадр с красными полями вдоль длинной стороны: центральный кроп их срезает."""
    width, height = size
    image = Image.new('RGB', size, BACKGROUND)
    draw = ImageDraw.Draw(image)
    margin = abs(width - height) // 2
    if width > height:
        draw.rectangle((0, 0, margin - 1, height), fill=RED)
        draw.rectangle((width - margin, 0, width, height), fill=RED)
    elif height > width:
        draw.rectangle((0, 0, width, margin - 1), fill=RED)
        draw.rectangle((0, height - margin, width, height), fill=RED)
    draw.ellipse((width // 2 - 200, height // 2 - 200, width // 2 + 200, height // 2 + 200), fill=(240, 200, 120))
    exif = Image.Exif()
    exif[0x0110] = 'SyntheticCam'  # Model
    exif[0x0132] = '2026:10:16 19:00:00'  # DateTime
    image.save(path, 'JPEG', quality=95, exif=exif)
    return path


def test_synthetic_source_really_has_exif(tmp_path):
    src = _synthetic(tmp_path / 'card.jpg')
    with Image.open(src) as image:
        assert image.getexif()


def test_normalize_square_crop_resize_weight_and_no_exif(tmp_path):
    src = _synthetic(tmp_path / 'card.jpg')
    dst = tmp_path / 'out' / 'card.jpg'

    result = normalize(src, dst)

    assert result.source_size == (1200, 900)
    assert dst.stat().st_size <= MAX_BYTES == 150 * 1024
    assert result.bytes == dst.stat().st_size
    with Image.open(dst) as out:
        assert out.format == 'JPEG'
        assert out.size == (SIZE, SIZE) == (800, 800)
        assert not out.getexif()
        assert 'exif' not in out.info
        # Красные поля срезаны кропом, фон по краям остался исходным.
        for xy in ((5, 400), (794, 400), (400, 5), (400, 794)):
            r, g, b = out.getpixel(xy)
            assert r < 80 and abs(r - BACKGROUND[0]) < 25 and abs(b - BACKGROUND[2]) < 25


def test_center_square_keeps_square_untouched():
    image = Image.new('RGB', (640, 640))
    assert center_square(image) is image
    assert center_square(Image.new('RGB', (300, 500))).size == (300, 300)


def test_normalize_refuses_when_limit_unreachable(tmp_path):
    src = _synthetic(tmp_path / 'card.jpg')
    with pytest.raises(ValueError, match='лимит'):
        normalize(src, tmp_path / 'out.jpg', max_bytes=1024)
    assert not (tmp_path / 'out.jpg').exists()


def test_cli_keeps_file_names(tmp_path, capsys):
    raw = tmp_path / 'raw'
    raw.mkdir()
    _synthetic(raw / 'bun_caramel.jpg')
    _synthetic(raw / 'raf_coconut.jpg', size=(900, 1600))
    dst = tmp_path / 'demo' / 'images'

    assert main(['--src', str(raw), '--dst', str(dst)]) == 0
    assert sorted(p.name for p in dst.iterdir()) == ['bun_caramel.jpg', 'raf_coconut.jpg']
    assert 'Готово: 2/2' in capsys.readouterr().out
