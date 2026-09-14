from types import SimpleNamespace

import pytest
from ocrmypdf.pluginspec import OrientationConfidence
from PIL import Image, ImageDraw

from ocr_masivo import ocr_plugin as plugin
from ocr_masivo.config import Config
from ocr_masivo.processor import command


@pytest.fixture
def preview(tmp_path):
    path = tmp_path / "preview.png"
    image = Image.new("L", (300, 400), 255)
    ImageDraw.Draw(image).rectangle((20, 20, 150, 100), fill=0)
    image.save(path)
    return path


@pytest.fixture
def options():
    return SimpleNamespace(
        rotate_pages_threshold=15, languages=["spa"], tesseract=SimpleNamespace(non_ocr_timeout=60)
    )


def test_confident_osd_avoids_extra_ocr(monkeypatch, preview, options):
    monkeypatch.setattr(
        plugin.TesseractOcrEngine, "get_orientation", lambda *a: OrientationConfidence(90, 20)
    )
    monkeypatch.setattr(plugin, "read_preview", lambda *a: pytest.fail("Unnecessary OCR"))
    assert plugin.LocalEngine.get_orientation(preview, options).angle == 90


def test_strong_upright_read_avoids_other_three_rotations(monkeypatch, preview, options):
    original = preview.read_bytes()
    calls = []
    monkeypatch.setattr(plugin.TesseractOcrEngine, "get_orientation", lambda *a: OrientationConfidence(0, 5))

    def read(path, languages, timeout):
        calls.append(path.name)
        return dict(
            score=150,
            confidence=80,
            alphanumeric=900,
            horizontal_word_ratio=0.98,
            plausible_ratio=0.25,
        )

    monkeypatch.setattr(plugin, "read_preview", read)
    assert plugin.LocalEngine.get_orientation(preview, options) == (0, 15)
    assert calls == ["rotation-0.png"]
    assert preview.read_bytes() == original


@pytest.mark.parametrize(
    "change",
    [
        {"confidence": 44},
        {"alphanumeric": 99},
        {"horizontal_word_ratio": 0.79},
        {"plausible_ratio": 0.14},
    ],
)
def test_weak_upright_signal_still_uses_all_rotations(monkeypatch, preview, options, change):
    calls = []
    monkeypatch.setattr(plugin.TesseractOcrEngine, "get_orientation", lambda *a: OrientationConfidence(0, 5))
    base = dict(
        score=100,
        confidence=80,
        alphanumeric=900,
        horizontal_word_ratio=0.98,
        plausible_ratio=0.25,
    )

    def read(path, languages, timeout):
        angle = int(path.stem.split("-")[1])
        calls.append(angle)
        return {**base, **change, "score": 200 if angle == 90 else 100}

    monkeypatch.setattr(plugin, "read_preview", read)
    assert plugin.LocalEngine.get_orientation(preview, options).angle == 90
    assert calls == [0, 90, 180, 270]


@pytest.mark.parametrize("angle", [0, 90, 180, 270])
def test_fallback_rotation_and_preservation(monkeypatch, preview, options, angle):
    original = preview.read_bytes()
    calls = []
    monkeypatch.setattr(plugin.TesseractOcrEngine, "get_orientation", lambda *a: OrientationConfidence(0, 0))

    def read(path, languages, timeout):
        rotation = int(path.stem.split("-")[1])
        calls.append(rotation)
        assert 0 < timeout <= 15
        return dict(score=200 if rotation == angle else 80, confidence=90, alphanumeric=100)

    monkeypatch.setattr(plugin, "read_preview", read)
    assert plugin.LocalEngine.get_orientation(preview, options).angle == angle
    assert calls == [0, 90, 180, 270]
    assert preview.read_bytes() == original


def test_ambiguous_fallback_does_not_rotate(monkeypatch, preview, options):
    monkeypatch.setattr(plugin.TesseractOcrEngine, "get_orientation", lambda *a: OrientationConfidence(90, 2))
    monkeypatch.setattr(plugin, "read_preview", lambda *a: dict(score=100, confidence=80, alphanumeric=90))
    assert plugin.LocalEngine.get_orientation(preview, options) == (0, 0)


def test_failed_fallback_does_not_rotate(monkeypatch, preview, options):
    monkeypatch.setattr(plugin.TesseractOcrEngine, "get_orientation", lambda *a: OrientationConfidence(90, 2))

    def fail(*args):
        raise plugin.subprocess.TimeoutExpired("tesseract", 15)

    monkeypatch.setattr(plugin, "read_preview", fail)
    assert plugin.LocalEngine.get_orientation(preview, options) == (0, 0)


def test_filter_preserves_source_dimensions_and_dpi(monkeypatch):
    monkeypatch.setattr(plugin, "downsample", lambda page, image: image)
    monkeypatch.setenv("OCR_LOCAL_MEJORA", "1")
    image = Image.new("RGB", (40, 40), (200, 200, 200))
    ImageDraw.Draw(image).line((0, 20, 39, 20), fill=(150, 150, 150))
    image.info["dpi"] = (300, 300)
    before = image.tobytes()
    result = plugin.filter_ocr_image(None, image)
    assert result.size == image.size and result.info["dpi"] == (300, 300)
    assert result.getpixel((20, 20)) < result.getpixel((20, 10))
    assert image.tobytes() == before


def test_pdf_only_defaults_and_disable_switches():
    config = Config()
    assert config.workers == 1 and not config.generar_txt
    assert "--plugin" in command(config, "in.pdf", "out.pdf")
    config.orientacion_robusta = config.mejorar_escaneo = False
    args = command(config, "in.pdf", "out.pdf")
    assert "--plugin" not in args and "--oversample" not in args


def test_cli_progress_on_windows_legacy_encoding(tmp_path):
    import os
    import subprocess
    import sys

    source = tmp_path / "entrada"
    source.mkdir()
    args = [sys.executable, "-m", "ocr_masivo", "procesar"]
    for name in ("entrada", "salida", "texto", "errores", "logs"):
        args.extend([f"--{name}", str(tmp_path / name)])
    result = subprocess.run(
        args, capture_output=True, timeout=30, env={**os.environ, "PYTHONIOENCODING": "cp1252"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
