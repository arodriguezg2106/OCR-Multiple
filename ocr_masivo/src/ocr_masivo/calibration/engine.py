import csv
import io
import json
import os
import subprocess
import time

import pymupdf
from PIL import Image, ImageEnhance, ImageOps

from ..utils import atomic_text, sha256, temporary


def save_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def run_tesseract(executable, image, base, data, psm=3, language="spa", dpi=300):
    signature = dict(
        image_sha256=sha256(image),
        model_sha256=sha256(data / (language + ".traineddata")),
        psm=psm,
        language=language,
        dpi=dpi,
        executable=str(executable),
    )
    meta = base.with_suffix(".json")
    if meta.exists():
        stored = json.loads(meta.read_text(encoding="utf-8"))
        if stored["signature"] != signature:
            raise ValueError(f"La variante existente no coincide: {base}")
        return stored
    base.parent.mkdir(parents=True, exist_ok=True)
    args = [str(executable), str(image), str(base), "-l", language, "--psm", str(psm), "--dpi", str(dpi)]
    args += ["txt", "tsv"] if psm != 0 else []
    start = time.monotonic()
    result = subprocess.run(
        args,
        capture_output=True,
        shell=False,
        timeout=360,
        env={**os.environ, "TESSDATA_PREFIX": str(data), "OMP_THREAD_LIMIT": "1"},
    )
    atomic_text(base.with_suffix(".stdout.log"), result.stdout.decode("utf-8", errors="replace"))
    atomic_text(base.with_suffix(".stderr.log"), result.stderr.decode("utf-8", errors="replace"))
    words = []
    tsv = base.with_suffix(".tsv")
    if tsv.exists():
        for row in csv.DictReader(
            io.StringIO(tsv.read_text(encoding="utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE
        ):
            if row.get("text", "").strip() and row["level"] == "5":
                x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
                words.append(
                    dict(
                        text=row["text"],
                        confidence=float(row["conf"]),
                        box=[x, y, x + w, y + h],
                        line=[row["block_num"], row["par_num"], row["line_num"]],
                    )
                )
    with Image.open(image) as im:
        size = im.size
    record = dict(
        signature=signature,
        command=args,
        returncode=result.returncode,
        elapsed=time.monotonic() - start,
        words=words,
        size=size,
        text=base.with_suffix(".txt").read_text(encoding="utf-8")
        if base.with_suffix(".txt").exists()
        else "",
        stderr=result.stderr.decode("utf-8", errors="replace"),
    )
    save_json(meta, record)
    return record


def render(source, index, target, dpi, angle=0, preprocess="none"):
    if target.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(source) as doc:
        pix = doc[index].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72).prerotate(angle), alpha=False)
        im = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    if preprocess in {"gray", "adaptive"}:
        im = ImageEnhance.Contrast(ImageOps.grayscale(im)).enhance(1.15)
    if preprocess == "adaptive":
        import cv2
        import numpy as np

        im = Image.fromarray(
            cv2.adaptiveThreshold(
                np.array(im), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 15
            )
        )
    temp = temporary(target, ".partial.png")
    try:
        im.save(temp, format="PNG", dpi=(dpi, dpi))
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target


def transform_box(box, angle):
    x0, y0, x1, y1 = box
    points = [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]
    for _ in range((angle % 360) // 90):
        points = [(1 - y, x) for x, y in points]
    return [
        min(p[0] for p in points),
        min(p[1] for p in points),
        max(p[0] for p in points),
        max(p[1] for p in points),
    ]


def region_words(record, box):
    width, height = record["size"]
    x0, y0, x1, y1 = [box[0] * width, box[1] * height, box[2] * width, box[3] * height]
    selected = [
        w
        for w in record["words"]
        if x0 <= (w["box"][0] + w["box"][2]) / 2 <= x1 and y0 <= (w["box"][1] + w["box"][3]) / 2 <= y1
    ]
    # Preserve reading order established by the OCR engine; no matching against expected strings.
    return selected
