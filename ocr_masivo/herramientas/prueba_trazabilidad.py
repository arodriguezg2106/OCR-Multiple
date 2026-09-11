"""Prueba local explícita sobre originales; limita documentos por número de páginas."""

import argparse
import csv
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import ExitStack
from pathlib import Path

import pymupdf

from ocr_masivo.config import Config
from ocr_masivo.database import Database
from ocr_masivo.inventory import inventory
from ocr_masivo.processor import SUCCESSES, OCRmyPDFEngine, cleanup_owned, process_one, reusable
from ocr_masivo.reporter import report
from ocr_masivo.utils import atomic_text, batch_lock, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrada", type=Path, required=True)
    parser.add_argument("--destino", type=Path, required=True)
    parser.add_argument("--max-paginas", type=int, default=20, help="0: todos")
    args = parser.parse_args()
    tessdata = Path(__file__).parent / "tessdata"
    if tessdata.is_dir():
        os.environ["TESSDATA_PREFIX"] = str(tessdata.resolve())
    c = Config(
        entrada=args.entrada,
        salida=args.destino / "pdf",
        texto=args.destino / "texto",
        errores=args.destino / "errores",
        logs=args.destino / "logs",
    ).validate()
    db = Database(c.logs / "estado.sqlite3")
    logging.basicConfig(
        filename=c.logs / "ocr_masivo.log",
        encoding="utf-8",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    start = time.monotonic()
    with ExitStack() as stack:
        for folder in sorted({c.logs, c.salida, c.texto}, key=str):
            stack.enter_context(batch_lock(folder))
        rows = inventory(c, db)
        selected = [r for r in rows if not args.max_paginas or r["paginas_original"] <= args.max_paginas]
        db.setting("config", c.serialize())
        db.setting("active_ids", [r["id"] for r in selected])
        print(
            f"Seleccionados: {len(selected)} documentos, {sum(r['paginas_original'] for r in selected)} páginas",
            flush=True,
        )
        stop = threading.Event()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = []
            for row in sorted(selected, key=lambda r: r["paginas_original"]):
                cleanup_owned(c, row)
                if row["estado"] in SUCCESSES and reusable(c, row):
                    row["estado"] = "already_completed"
                    db.save(row)
                else:
                    futures.append(pool.submit(process_one, c, db, row, OCRmyPDFEngine(), stop))
            for future in as_completed(futures):
                row = future.result()
                print(f"{row['ruta_relativa']}: {row['estado']}; {row['duracion_segundos']} s", flush=True)
        db.setting("elapsed", time.monotonic() - start)
        selected = [db.get(r["id"]) for r in selected]
        path, summary = report(c, db, selected)
        audit = []
        for row in selected:
            intact = sha256(row["archivo_original"]) == row["hash_original"]
            if row["estado"] not in SUCCESSES:
                continue
            with (
                pymupdf.open(row["archivo_original"]) as original,
                pymupdf.open(row["archivo_resultado"]) as result,
            ):
                for index, page in enumerate(result):
                    text = page.get_text()
                    audit.append(
                        {
                            "archivo": row["ruta_relativa"],
                            "pagina": index + 1,
                            "hash_original": row["hash_original"],
                            "original_intacto": intact,
                            "caracteres_antes": len(original[index].get_text()),
                            "caracteres_despues": len(text),
                            "pdf_original": row["archivo_original"],
                            "pdf_ocr": row["archivo_resultado"],
                        }
                    )
        if audit:
            import io

            out = io.StringIO(newline="")
            writer = csv.DictWriter(out, fieldnames=list(audit[0]))
            writer.writeheader()
            writer.writerows(audit)
            atomic_text(c.logs / "trazabilidad_paginas.csv", "\ufeff" + out.getvalue())
        print(json.dumps(summary, ensure_ascii=False), flush=True)
        print(f"Reporte: {path}", flush=True)
        return 0 if all(r["estado"] in SUCCESSES for r in selected) else 2


if __name__ == "__main__":
    raise SystemExit(main())
