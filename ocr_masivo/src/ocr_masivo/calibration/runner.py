import argparse
import json
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pymupdf

from ..config import Config
from ..processor import command
from ..utils import atomic_text, sha256
from ..validator import inspect_pdf, validate_result
from .engine import render, run_tesseract, save_json
from .quality import choose_orientation, score_reading


def prepare(project, output):
    baseline = project / "prueba_resultados/auditoria_precision"
    if output == baseline or baseline in output.parents:
        raise ValueError("La salida debe estar separada de la línea base.")
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "preservacion.json"
    if not manifest.exists():
        protected = list(baseline.rglob("*")) + list((project / "Prueba").glob("*.pdf"))
        protected += list((project / "prueba_resultados/pdf").glob("*.pdf"))
        hashes = {str(p): sha256(p) for p in protected if p.is_file()}
        save_json(manifest, hashes)
    verify(output)
    for name in (
        "muestra.json",
        "referencia_visual.json",
        "referencia_visual.sha256",
        "comparacion_60_campos.json",
    ):
        target = output / name
        if not target.exists():
            shutil.copyfile(baseline / name, target)
        if sha256(target) != sha256(baseline / name):
            raise ValueError("La referencia copiada no coincide.")
    sample = json.loads((output / "muestra.json").read_text(encoding="utf-8"))
    if len(sample) != 12:
        raise ValueError("Esta calibración exige la muestra original de 12 páginas.")
    return sample


def verify(output):
    hashes = json.loads((output / "preservacion.json").read_text(encoding="utf-8"))
    for name, digest in hashes.items():
        if sha256(Path(name)) != digest:
            raise ValueError(f"Se modificó un archivo protegido: {name}")


def orient_page(project, output, page, tess):
    start = time.monotonic()
    folder = output / "orientation" / page["id"]
    decision = folder / "decision.json"
    if decision.exists():
        return json.loads(decision.read_text(encoding="utf-8"))
    source = project / "Prueba" / page["archivo"]
    alternatives = []
    fast = project / "herramientas/tessdata"
    for angle in (0, 90, 180, 270):
        img = render(source, page["pagina"] - 1, folder / f"{angle}.png", 180, angle)
        reading = run_tesseract(tess, img, folder / f"ocr_{angle}", fast, dpi=180)
        alternatives.append(dict(angle=angle, **score_reading(reading["words"])))
    osd = run_tesseract(tess, folder / "0.png", folder / "osd", fast, psm=0, language="osd", dpi=180)
    osdfile = folder / "osd.osd"
    osdtext = osdfile.read_text(encoding="utf-8") if osdfile.exists() else osd["stderr"]
    detected = {k: v for k, v in re.findall(r"([^\n:]+):\s*([^\n]+)", osdtext)}
    chosen = choose_orientation(alternatives, detected)
    with pymupdf.open(source) as doc:
        chosen["original_pdf_rotation"] = doc[page["pagina"] - 1].rotation
    chosen.update(page=page, elapsed=time.monotonic() - start)
    save_json(decision, chosen)
    print(f"Orientación {page['id']}: {chosen['angle']}°, ambigua={chosen['ambiguous']}", flush=True)
    return chosen


def pdf_reading(project, output, page, profile, angle):
    folder = output / profile / page["id"]
    meta = folder / "reading.json"
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    source = project / "Prueba" / page["archivo"]
    inspect_pdf(source)
    sample = folder / "sample.pdf"
    if not sample.exists():
        with pymupdf.open(source) as original, pymupdf.open() as derived:
            derived.insert_pdf(original, from_page=page["pagina"] - 1, to_page=page["pagina"] - 1)
            derived[0].set_rotation((derived[0].rotation + angle) % 360)
            derived.save(sample)
    temp = folder / "result.partial.pdf"
    target = folder / "result.pdf"
    start = time.monotonic()
    cfg = Config(orientacion_robusta=False, mejorar_escaneo=False)
    args = command(cfg, sample, temp)
    with (folder / "stdout.log").open("wb") as stdout, (folder / "stderr.log").open("wb") as stderr:
        result = subprocess.run(
            args,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            timeout=480,
            env={
                **os.environ,
                "TESSDATA_PREFIX": str(project / "herramientas/tessdata"),
                "OMP_THREAD_LIMIT": "1",
            },
        )
    if result.returncode:
        raise RuntimeError(
            f"{profile}/{page['id']}: OCRmyPDF código {result.returncode}; consulte stderr.log"
        )
    validate_result(temp, 1)
    os.replace(temp, target)
    words = []
    with pymupdf.open(target) as doc:
        p = doc[0]
        # Text coordinates use unrotated page space. Map to visual space for ROI correspondence.
        for x0, y0, x1, y1, text, block, line, _ in p.get_text("words"):
            box = pymupdf.Rect(x0, y0, x1, y1) * p.rotation_matrix
            words.append(dict(text=text, box=list(box), confidence=None, line=[block, line]))
        record = dict(
            words=words,
            size=[p.rect.width, p.rect.height],
            text=p.get_text(),
            elapsed=time.monotonic() - start,
            profile=profile,
            angle=angle,
            command=args,
            returncode=result.returncode,
            pdf_rotation=p.rotation,
        )
    atomic_text(folder / "raw.txt", record["text"])
    save_json(meta, record)
    print(f"Perfil {profile}: {page['id']} terminado", flush=True)
    return record


def high_reading(project, output, page, tess, angle):
    folder = output / "C" / page["id"]
    img = render(
        project / "Prueba" / page["archivo"], page["pagina"] - 1, folder / "image.png", 300, angle, "gray"
    )
    result = run_tesseract(tess, img, folder / "reading", project / "herramientas/tessdata_best", dpi=300)
    print(f"Perfil C: {page['id']} terminado", flush=True)
    return result


def table_reading(project, output, page, tess, angle):
    folder = output / "D" / page["id"]
    source = project / "Prueba" / page["archivo"]
    gray = render(source, page["pagina"] - 1, folder / "gray.png", 400, angle, "gray")
    alternatives = []
    for psm in (3, 4, 6, 11):
        result = run_tesseract(
            tess, gray, folder / f"gray_psm{psm}", project / "herramientas/tessdata_best", psm=psm, dpi=400
        )
        alternatives.append(
            dict(psm=psm, preprocess="gray", **score_reading(result["words"]), elapsed=result["elapsed"])
        )
    best = max(alternatives, key=lambda a: a["score"])
    adaptive = render(source, page["pagina"] - 1, folder / "adaptive.png", 400, angle, "adaptive")
    result = run_tesseract(
        tess,
        adaptive,
        folder / f"adaptive_psm{best['psm']}",
        project / "herramientas/tessdata_best",
        psm=best["psm"],
        dpi=400,
    )
    candidate = dict(
        psm=best["psm"], preprocess="adaptive", **score_reading(result["words"]), elapsed=result["elapsed"]
    )
    alternatives.append(candidate)
    if candidate["score"] > best["score"] * 1.02:
        best = candidate
    save_json(
        folder / "selection.json",
        dict(
            chosen=best,
            alternatives=alternatives,
            method="Puntuación ciega; adaptativa solo si mejora >2% frente a gris.",
        ),
    )
    detect_tables(gray, folder / "tables.json")
    print(f"Perfil D: {page['id']} PSM {best['psm']} {best['preprocess']}", flush=True)


def detect_tables(image, target):
    import cv2
    import numpy as np

    im = cv2.imdecode(np.fromfile(image, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    bw = cv2.adaptiveThreshold(im, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 15)
    horizontal = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(30, im.shape[1] // 20), 1))
    )
    vertical = cv2.morphologyEx(
        bw, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(30, im.shape[0] // 20)))
    )
    mask = cv2.dilate(horizontal | vertical, np.ones((7, 7), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [list(cv2.boundingRect(c)) for c in contours if cv2.contourArea(c) > im.size * 0.01]
    save_json(
        target,
        dict(
            regions=boxes,
            horizontal_pixels=int(np.count_nonzero(horizontal)),
            vertical_pixels=int(np.count_nonzero(vertical)),
            line_removal=False,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description="Calibración limitada a la muestra congelada de 12 páginas.")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--stage", choices=["orientation", "A", "B", "C", "D", "all"], default="all")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    project = args.project.resolve()
    output = project / "prueba_resultados/auditoria_precision_v2"
    pages = prepare(project, output)
    tess = Path(shutil.which("tesseract") or "C:/Program Files/Tesseract-OCR/tesseract.exe")
    if not tess.exists():
        raise RuntimeError("Instale Tesseract antes de calibrar.")
    stages = ["orientation", "A", "B", "C", "D"] if args.stage == "all" else [args.stage]
    for stage in stages:
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = []
            for page in pages:
                if stage == "orientation":
                    future = pool.submit(orient_page, project, output, page, tess)
                else:
                    angle = (
                        0
                        if stage == "A"
                        else json.loads(
                            (output / "orientation" / page["id"] / "decision.json").read_text(
                                encoding="utf-8"
                            )
                        )["angle"]
                    )
                    if stage in {"A", "B"}:
                        future = pool.submit(pdf_reading, project, output, page, stage, angle)
                    elif stage == "C":
                        future = pool.submit(high_reading, project, output, page, tess, angle)
                    else:
                        future = pool.submit(table_reading, project, output, page, tess, angle)
                futures.append(future)
            for future in as_completed(futures):
                future.result()
        save_json(
            output / f"timing_{stage}.json", dict(wall_seconds=time.monotonic() - start, workers=args.workers)
        )
    verify(output)


if __name__ == "__main__":
    main()
