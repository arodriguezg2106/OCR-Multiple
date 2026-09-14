"""Bounded orientation fallback and conservative OCR-only image enhancement."""

import csv
import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from ocrmypdf import hookimpl
from ocrmypdf.builtin_plugins.tesseract_ocr import TesseractOcrEngine
from ocrmypdf.builtin_plugins.tesseract_ocr import filter_ocr_image as downsample
from ocrmypdf.pluginspec import OrientationConfidence
from PIL import Image, ImageEnhance, ImageStat

from ocr_masivo.calibration.quality import choose_orientation, score_reading

log = logging.getLogger(__name__)


def upright_conclusive(reading):
    """Accept an upright page early only when independent signals agree."""
    return (
        reading.get("confidence", 0) >= 45
        and reading.get("alphanumeric", 0) >= 100
        and reading.get("horizontal_word_ratio", 0) >= 0.80
        and reading.get("plausible_ratio", 0) >= 0.15
    )


def read_preview(path, languages, timeout):
    executable = shutil.which("tesseract") or "C:/Program Files/Tesseract-OCR/tesseract.exe"
    result = subprocess.run(
        [executable, str(path), "stdout", "-l", "+".join(languages), "--psm", "3", "tsv"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        shell=False,
    )
    if result.returncode:
        raise RuntimeError(f"Tesseract preview exit {result.returncode}")
    words = []
    for row in csv.DictReader(io.StringIO(result.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not row.get("text", "").strip():
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        words.append(dict(text=row["text"], confidence=float(row["conf"]), box=[x, y, x + w, y + h]))
    return score_reading(words)


class LocalEngine(TesseractOcrEngine):
    @staticmethod
    def get_orientation(input_file, options):
        osd = TesseractOcrEngine.get_orientation(input_file, options)
        threshold = max(15, options.rotate_pages_threshold)
        if osd.confidence >= threshold:
            log.info("Orientación local: OSD concluyente (%s, %.2f)", osd.angle, osd.confidence)
            return osd
        start = time.monotonic()
        budget = min(60, options.tesseract.non_ocr_timeout)
        alternatives = []
        # Preview only, at most 1800 pixels per axis. Never modify the input PDF.
        with Image.open(input_file) as source, tempfile.TemporaryDirectory(prefix="ocr-local-") as folder:
            preview = source.convert("L")
            preview.thumbnail((1800, 1800))
            if ImageStat.Stat(preview).stddev[0] < 2:
                log.warning("Orientación local: página casi uniforme; se conserva orientación para revisión.")
                return OrientationConfidence(0, 0)
            for angle in (0, 90, 180, 270):
                remaining = budget - (time.monotonic() - start)
                if remaining <= 0:
                    break
                path = Path(folder) / f"rotation-{angle}.png"
                preview.rotate(angle, expand=True).save(path, dpi=(150, 150))
                try:
                    reading = dict(angle=angle, **read_preview(path, options.languages, min(15, remaining)))
                    alternatives.append(reading)
                    if angle == 0 and upright_conclusive(reading):
                        log.info(
                            "Orientación local: lectura vertical normal concluyente; "
                            "se omiten los otros tres giros: %s",
                            json.dumps(reading),
                        )
                        return OrientationConfidence(0, threshold)
                except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                    log.warning("Orientación local: alternativa %s falló: %s", angle, exc)
                    break
        if len(alternatives) != 4:
            log.warning("Orientación local: prueba incompleta; se conserva orientación para revisión.")
            return OrientationConfidence(0, 0)
        decision = choose_orientation(alternatives, osd=dict(angle=osd.angle, confidence=osd.confidence))
        log.info("Orientación local (giros antihorarios de corrección): %s", json.dumps(decision))
        if decision["ambiguous"]:
            log.warning("Orientación local: decisión ambigua; se conserva orientación para revisión.")
            return OrientationConfidence(0, 0)
        # OCRmyPDF's pipeline applies this correction counterclockwise, like PIL.rotate.
        return OrientationConfidence(decision["angle"], threshold)


@hookimpl
def get_ocr_engine(options):
    if os.environ.get("OCR_LOCAL_ORIENTACION") == "1":
        return LocalEngine()
    return None


@hookimpl
def filter_ocr_image(page, image):
    image = downsample(page, image)
    if os.environ.get("OCR_LOCAL_MEJORA") != "1" or image.mode == "1":
        return image
    # No thresholding or line removal: preserve faint digits and table borders.
    metadata = image.info.copy()
    gray = image.convert("L")
    if ImageStat.Stat(gray).stddev[0] < 45:
        gray = ImageEnhance.Contrast(gray).enhance(1.10)
        gray.info.update(metadata)
        log.info("Mejora local: contraste suave 1.10 solo en imagen de OCR.")
        return gray
    return image
