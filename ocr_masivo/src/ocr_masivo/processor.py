import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import ExitStack
from pathlib import Path
from typing import Protocol

from rich.progress import BarColumn, SpinnerColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

from .inventory import inventory
from .progress import BatchProgress, last_activity
from .utils import atomic_text, batch_lock, now, sha256, temporary
from .validator import InvalidPDF, extract_text, inspect_pdf, validate_result

FAILURES = {"failed", "encrypted", "signed", "validation_failed"}
SUCCESSES = {"completed", "already_completed"}


class Engine(Protocol):
    def run(self, config, source, target, stdout, stderr, stop): ...


def command(config, source, target):
    args = [
        sys.executable,
        "-m",
        "ocrmypdf",
        "-l",
        config.idioma,
        "--mode",
        config.modo,
        "--output-type",
        config.tipo_salida,
        "--optimize",
        str(config.optimizacion),
        "--jobs",
        "1",
        "--tesseract-timeout",
        str(config.timeout_pagina),
    ]
    if config.rotacion:
        args.append("--rotate-pages")
    if (config.rotacion and config.orientacion_robusta) or config.mejorar_escaneo:
        args.extend(["--plugin", "ocr_masivo.ocr_plugin"])
    if config.mejorar_escaneo:
        args.extend(["--oversample", "300"])
    if config.inclinacion:
        args.append("--deskew")
    if config.megapixeles is not None:
        args.extend(["--skip-big", str(config.megapixeles)])
    return args + [str(source), str(target)]


class OCRmyPDFEngine:
    def run(self, config, source, target, stdout, stderr, stop):
        with stdout.open("wb") as out, stderr.open("wb") as err:
            proc = subprocess.Popen(
                command(config, source, target),
                stdout=out,
                stderr=err,
                shell=False,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
                env={
                    **os.environ,
                    "OMP_THREAD_LIMIT": "1",
                    "OCR_LOCAL_ORIENTACION": str(int(config.rotacion and config.orientacion_robusta)),
                    "OCR_LOCAL_MEJORA": str(int(config.mejorar_escaneo)),
                },
            )
            while proc.poll() is None:
                # Finish the current isolated document on Ctrl+C; never orphan a child writing a partial.
                time.sleep(0.2)
            return proc.returncode


def rejection(code, message):
    lower = message.lower()
    if "digitalsignature" in lower or "digitally signed" in lower or "digital signature" in lower:
        return "signed"
    if code == 8 or "encryptedpdf" in lower or "password" in lower:
        return "encrypted"
    return "failed"


def reusable(config, row):
    try:
        pdf = config.salida / row["ruta_relativa"]
        if str(pdf) != row["archivo_resultado"] or sha256(pdf) != row["hash_resultado"]:
            return False
        validate_result(pdf, row["paginas_original"])
        if config.generar_txt:
            txt = config.texto / Path(row["ruta_relativa"]).with_suffix(".txt")
            if str(txt) != row["archivo_texto"] or sha256(txt) != row.get("hash_texto"):
                return False
        return True
    except Exception:
        return False


def record_error(config, row, detail=""):
    """Errors are diagnostic manifests, not relocated originals or duplicated sensitive PDFs."""
    target = config.errores / (row["ruta_relativa"] + ".error.json")
    atomic_text(target, json.dumps({**row, "detalle_tecnico": detail}, indent=2, ensure_ascii=False))


def process_one(config, db, row, engine, stop):
    started = time.monotonic()
    row.update(estado="processing", fecha_inicio=now(), fecha_fin="", mensaje_error="", codigo_salida=None)
    db.save(row)
    tmp = txt_tmp = None
    detail = ""
    try:
        source = Path(row["archivo_original"])
        if sha256(source) != row["hash_original"]:
            raise InvalidPDF("El original cambió después del inventario. Ejecute de nuevo el lote.")
        pages = inspect_pdf(source)
        row["paginas_original"] = pages
        target = config.salida / row["ruta_relativa"]
        txt = config.texto / Path(row["ruta_relativa"]).with_suffix(".txt")
        # Refuse preexisting unrelated files, including outputs from other batches.
        if target.exists() and str(target) != row["archivo_resultado"]:
            raise InvalidPDF("Ya existe un resultado no reconocido; muévalo o elija otra salida.")
        if config.generar_txt and txt.exists() and str(txt) != row["archivo_texto"]:
            raise InvalidPDF("Ya existe un TXT no reconocido; muévalo o elija otra carpeta de texto.")
        tmp = temporary(target, ".partial.pdf")
        # Persist ownership before spawning so crash leftovers can be safely recovered.
        row["partial_pdf"] = str(tmp)
        db.save(row)
        log_base = config.logs / row["id"]
        stdout, stderr = log_base.with_suffix(".stdout.log"), log_base.with_suffix(".stderr.log")
        code = engine.run(config, source, tmp, stdout, stderr, stop)
        row["codigo_salida"] = code
        message = stderr.read_text(encoding="utf-8", errors="replace") if stderr.exists() else ""
        if code:
            raise InvalidPDF(
                f"OCRmyPDF rechazó el documento (código {code}). {message[-2000:]}", rejection(code, message)
            )
        count, chars = validate_result(tmp, pages)
        if sha256(source) != row["hash_original"]:
            raise InvalidPDF("El original cambió durante el OCR; resultado descartado.")
        if config.generar_txt:
            txt_tmp = temporary(txt, ".partial.txt")
            row["partial_txt"] = str(txt_tmp)
        # Save intended hashes before publication: recover a crash between replace and completion.
        row.update(
            archivo_resultado=str(target),
            hash_resultado=sha256(tmp),
            paginas_resultado=count,
            caracteres_extraidos=chars,
            tamano_resultado=tmp.stat().st_size,
        )
        db.save(row)
        os.replace(tmp, target)
        if config.generar_txt:
            extract_text(target, txt_tmp)
            row.update(archivo_texto=str(txt), hash_texto=sha256(txt_tmp))
            db.save(row)
            os.replace(txt_tmp, txt)
        row.update(
            estado="completed", mensaje_error="" if chars else "Sin texto reconocido; revisar visualmente."
        )
        if "para revisión" in message:
            row["mensaje_error"] = "Orientación no concluyente en alguna página; revisar el PDF y el log."
        (config.errores / (row["ruta_relativa"] + ".error.json")).unlink(missing_ok=True)
    except InvalidPDF as exc:
        row.update(estado=exc.state, mensaje_error=str(exc))
        detail = traceback.format_exc()
    except Exception as exc:
        row.update(estado="failed", mensaje_error=f"Error al procesar el archivo: {exc}")
        detail = traceback.format_exc()
    finally:
        for partial in (tmp, txt_tmp):
            if partial:
                try:
                    partial.unlink(missing_ok=True)
                except OSError:
                    logging.exception("No se pudo limpiar el temporal %s", partial)
        row.update(fecha_fin=now(), duracion_segundos=round(time.monotonic() - started, 3))
        db.save(row)
        if row["estado"] in FAILURES:
            try:
                record_error(config, row, detail)
            except OSError:
                logging.exception("No se pudo escribir el diagnóstico de %s", row["ruta_relativa"])
        logging.info("%s: %s %s", row["ruta_relativa"], row["estado"], detail)
    return row


def cleanup_owned(config, row):
    for key, root, suffix in (
        ("partial_pdf", config.salida, ".partial.pdf"),
        ("partial_txt", config.texto, ".partial.txt"),
    ):
        if row.get(key):
            p = Path(row[key])
            if root in p.resolve().parents and p.name.startswith(".ocr-") and p.name.endswith(suffix):
                p.unlink(missing_ok=True)


def run_batch(config, db, only_failed=False, engine=None):
    engine = engine or OCRmyPDFEngine()
    stop = threading.Event()
    with ExitStack() as locks:
        for folder in sorted({config.logs, config.salida, config.texto}, key=str):
            locks.enter_context(batch_lock(folder))
        previous = {r["id"]: r for r in db.rows()}
        for row in previous.values():
            cleanup_owned(config, row)
        with BatchProgress(SpinnerColumn("line"), TextColumn("{task.description}")) as scan:
            scan.add_task("Inventariando PDF y verificando originales...", total=None)

            def on_inventory(count, relative):
                scan.status = f"Inventario: {count} PDF encontrados; leyendo archivo y calculando hash"
                scan.activity = relative

            rows = inventory(config, db, on_progress=on_inventory)
        db.setting("config", config.serialize())
        db.setting("active_ids", [r["id"] for r in rows])
        pending = []
        for row in rows:
            for root, relative in (
                (config.salida, row["ruta_relativa"]),
                (config.texto, str(Path(row["ruta_relativa"]).with_suffix(".txt"))),
                (config.errores, row["ruta_relativa"] + ".error.json"),
            ):
                path = root / relative
                if path.is_symlink() or root not in path.resolve().parents:
                    raise ValueError(f"La ruta de resultado sale de su carpeta o es un enlace: {path}")
            if only_failed and previous.get(row["id"], {}).get("estado") not in FAILURES:
                continue
            if row["estado"] in SUCCESSES and reusable(config, row):
                row["estado"] = "already_completed"
                db.save(row)
                continue
            if row["estado"] in SUCCESSES:
                row["estado"] = "pending"
            if row["estado"] in FAILURES and not (config.reintentar_fallidos or only_failed):
                record_error(config, row)
                continue
            pending.append(row)
        start = time.monotonic()
        with BatchProgress(
            SpinnerColumn("line"),
            TextColumn("{task.description}"),
            BarColumn(bar_width=20),
            TextColumn("{task.percentage:>3.0f}%"),
            TextColumn("Tiempo:"),
            TimeElapsedColumn(),
            TextColumn("Restante aprox.:"),
            TimeRemainingColumn(compact=True),
        ) as progress:
            task = progress.add_task("Lote", total=len(rows), completed=len(rows) - len(pending))
            iterator = iter(pending)
            with ThreadPoolExecutor(max_workers=config.workers) as pool:
                futures = {}

                def submit_next():
                    row = next(iterator, None)
                    if row:
                        futures[pool.submit(process_one, config, db, row, engine, stop)] = row

                for _ in range(config.workers):
                    submit_next()
                try:
                    while futures:
                        finished, _ = wait(futures, timeout=0.5, return_when=FIRST_COMPLETED)
                        for future in finished:
                            future.result()
                            del futures[future]
                            progress.advance(task)
                            submit_next()
                        completed = sum(r["estado"] in SUCCESSES for r in rows)
                        failed = sum(r["estado"] in FAILURES for r in rows)
                        progress.status = (
                            f"Total {len(rows)} | Completados {completed} | "
                            f"Fallidos {failed} | Pendientes {len(rows) - completed - failed}"
                        )
                        progress.activity = "\n".join(
                            f"Archivo: {r['ruta_relativa']}\n"
                            f"{last_activity((config.logs / r['id']).with_suffix('.stderr.log'))}"
                            for r in futures.values()
                        )
                        if progress.tasks[task].time_remaining is None and futures:
                            progress.activity += "\nEstimación pendiente: esperando documentos terminados."
                        progress.refresh()
                except KeyboardInterrupt:
                    stop.set()
                    progress.console.print(
                        "Detención solicitada: terminando documentos activos. Los demás quedan pendientes."
                    )
                    for future in futures:
                        future.result()
        db.setting("elapsed", time.monotonic() - start)
        return [db.get(r["id"]) for r in rows]
