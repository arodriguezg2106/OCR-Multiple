import argparse
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageOps

from .engine import region_words, render, run_tesseract, save_json, transform_box
from .quality import MONTHS, normalize, validate_field


def extract_value(kind, raw):
    """Locate typed tokens without access to expected values. Keep source text and spans separately."""
    text = re.sub(r"\s+", " ", raw).strip()
    months = "(?:" + "|".join(MONTHS) + ")"
    if kind == "fecha":
        patterns = [
            r"del\s+\d{1,3}\s+de\s+" + months + r"\s+al\s+\d{1,3}\s+de\s+" + months + r"\s+de\s+\d{4}",
            r"(?<!\d)\d{1,3}/\d{1,2}/\d{4}(?!\d)",
            r"(?<!\d)\d{1,3}\s+de\s+" + months + r"\s+(?:de|del)\s+\d{4}",
            months + r"\s+de\s+\d{4}",
        ]
    elif kind == "importe":
        patterns = [r"(?<![\w,.])(?:\(\$?\s*\d[\d,]*\.\d{2}\)|\$?\s*-?\d[\d,]*\.\d{2})(?!\d|[.,]\d)"]
    elif kind == "cuenta":
        patterns = [r"[A-Za-z0-9](?:[A-Za-z0-9.:,]*[A-Za-z0-9])?"]
    else:
        patterns = [r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*"]
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, re.IGNORECASE))
        if kind in {"cuenta", "folio"}:
            matches = [m for m in matches if any(c.isdigit() for c in m[0])]
        if matches:
            match = max(matches, key=lambda m: len(m[0])) if kind in {"cuenta", "folio"} else matches[0]
            return dict(
                value=match[0], normalized=normalize(match[0]), span=list(match.span()), raw_region=raw
            )
    # Unparseable text is retained. It is not silently emptied to hide recognition errors.
    return dict(value=text, normalized=normalize(text), span=[0, len(text)], raw_region=raw)


def read_region(record, box, kind):
    words = region_words(record, box)
    raw = " ".join(w["text"] for w in words)
    available = [w["confidence"] for w in words if w.get("confidence") is not None]
    value = extract_value(kind, raw)
    return dict(**value, confidence=sum(available) / len(available) if available else None, words=words)


def crop_score(reading, kind):
    v = extract_value(kind, reading["text"])
    words = reading["words"]
    conf = sum(max(0, w["confidence"]) for w in words) / max(1, len(words))
    issues = validate_field(kind, v["value"])
    valid = not any(i in issues for i in ("formato_invalido", "fecha_invalida", "sin_lectura"))
    # Favor a legible valid token, not proximity to a ground-truth value.
    return (
        conf + 20 * valid - 15 * ("confusion_alfanumerica" in issues) - 2 * max(0, len(words) - 12),
        v,
        conf,
    )


def field_trials(project, output, idx, page, kind, box, reference_angle, tess):
    folder = output / "D_fields" / f"{idx:02d}"
    chosenfile = folder / "selection.json"
    if chosenfile.exists():
        return json.loads(chosenfile.read_text(encoding="utf-8"))
    start = time.monotonic()
    decision = json.loads((output / "orientation" / page["id"] / "decision.json").read_text(encoding="utf-8"))
    chosenangle = decision["angle"]
    mapped = transform_box(box, chosenangle - reference_angle)
    source = project / "Prueba" / page["archivo"]
    variants = []
    folder.mkdir(parents=True, exist_ok=True)
    # Anatomical field regions are fixed annotations, same for every profile, independent of values.
    for prep in ("gray", "adaptive"):
        image = render(
            source, page["pagina"] - 1, output / "D" / page["id"] / (prep + ".png"), 400, chosenangle, prep
        )
        with Image.open(image) as im:
            pixels = [round(v * (im.width if i % 2 == 0 else im.height)) for i, v in enumerate(mapped)]
            crop = im.crop(pixels)
            angles = (0, 90, 180, 270) if crop.height > 1.3 * crop.width else (0,)
            for turn in angles:
                roi = ImageOps.expand(
                    crop.rotate(-turn, expand=True, fillcolor="white"), border=24, fill="white"
                )
                path = folder / f"{prep}_{turn}.png"
                roi.save(path, dpi=(400, 400))
                modes = (3, 4, 6, 11) if prep == "gray" else (max(variants, key=lambda a: a["score"])["psm"],)
                for psm in modes:
                    basename = f"{prep}_{turn}_psm{psm}"
                    result = run_tesseract(
                        tess,
                        path,
                        folder / basename,
                        project / "herramientas/tessdata_best",
                        psm=psm,
                        dpi=400,
                    )
                    score, value, conf = crop_score(result, kind)
                    variants.append(
                        dict(
                            psm=psm,
                            preprocess=prep,
                            local_angle=turn,
                            basename=basename,
                            score=score,
                            confidence=conf,
                            elapsed=result["elapsed"],
                            **value,
                        )
                    )
    graybest = max((v for v in variants if v["preprocess"] == "gray"), key=lambda a: a["score"])
    adaptivebest = max((v for v in variants if v["preprocess"] == "adaptive"), key=lambda a: a["score"])
    chosen = adaptivebest if adaptivebest["score"] > graybest["score"] * 1.02 else graybest
    record = dict(
        field_id=idx,
        chosen=chosen,
        alternatives=variants,
        orientation=chosenangle,
        box=mapped,
        elapsed=time.monotonic() - start,
        method="PSM 3/4/6/11 por región; selección sin referencia por confianza y formato; adaptativa exige mejora >2%.",
    )
    save_json(chosenfile, record)
    print(f"Región {idx:02d}: PSM {chosen['psm']} {chosen['preprocess']}", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    project = args.project.resolve()
    output = project / "prueba_resultados/auditoria_precision_v2"
    fields = json.loads((output / "referencia_visual.json").read_text(encoding="utf-8"))["campos"]
    pages = {p["id"]: p for p in json.loads((output / "muestra.json").read_text(encoding="utf-8"))}
    regions = json.loads(Path(__file__).with_name("regions.json").read_text(encoding="utf-8"))
    target = output / "regions.json"
    if target.exists() and json.loads(target.read_text(encoding="utf-8")) != regions:
        raise ValueError("Las regiones congeladas no coinciden.")
    save_json(target, regions)
    tess = Path(shutil.which("tesseract") or "C:/Program Files/Tesseract-OCR/tesseract.exe")
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                field_trials,
                project,
                output,
                idx,
                pages[f[0]],
                f[1],
                regions["boxes"][str(idx)],
                regions["reference_angles"][f[0]],
                tess,
            )
            for idx, f in enumerate(fields, 1)
        ]
        for future in as_completed(futures):
            future.result()
    save_json(
        output / "timing_D_fields.json", dict(wall_seconds=time.monotonic() - start, workers=args.workers)
    )


if __name__ == "__main__":
    main()
