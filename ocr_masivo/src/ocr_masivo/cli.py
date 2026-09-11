import argparse
import importlib.metadata
import importlib.util
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import fields
from pathlib import Path

from rich.console import Console

from .config import Config, load_config
from .database import Database
from .inventory import inventory
from .processor import FAILURES, run_batch
from .reporter import report
from .utils import batch_lock

console = Console()


def diagnostic(config):
    ok = sys.version_info >= (3, 11)
    console.print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    try:
        console.print(f"OCRmyPDF: {importlib.metadata.version('ocrmypdf')}")
        result = subprocess.run(
            [sys.executable, "-m", "ocrmypdf", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        if result.returncode:
            console.print(result.stderr)
            ok = False
    except Exception as exc:
        console.print(f"OCRmyPDF no disponible: {exc}. Ejecute: python -m pip install -r requirements.txt")
        ok = False
    tesseract = shutil.which("tesseract")
    if not tesseract:
        candidate = Path("C:/Program Files/Tesseract-OCR/tesseract.exe")
        tesseract = str(candidate) if candidate.exists() else None
    if tesseract:
        try:
            for flag in ("--version", "--list-langs"):
                result = subprocess.run(
                    [tesseract, flag],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=30,
                    shell=False,
                )
                console.print(result.stdout or result.stderr)
                ok &= result.returncode == 0
                if flag == "--list-langs":
                    languages = set(result.stdout.splitlines())
                    missing = {"spa", *config.idioma.split("+")} - languages
                    if missing:
                        console.print(
                            f"Faltan idiomas: {', '.join(sorted(missing))}. Consulte README: instalación de spa."
                        )
                        ok = False
        except (OSError, subprocess.TimeoutExpired) as exc:
            console.print(f"No se pudo ejecutar Tesseract: {exc}")
            ok = False
    else:
        console.print("Falta Tesseract. Ejecute: winget install -e --id UB-Mannheim.TesseractOCR")
        ok = False
    pdfium = importlib.util.find_spec("pypdfium2") is not None
    gs = shutil.which("gswin64c") or shutil.which("gs")
    if not gs:
        candidates = sorted(Path("C:/Program Files/gs").glob("gs*/bin/gswin64c.exe"))
        gs = str(candidates[-1]) if candidates else None
    console.print(
        f"Rasterizador PDFium: {'disponible' if pdfium else 'ausente'}; Ghostscript: {gs or 'ausente'}"
    )
    if not (pdfium or gs):
        ok = False
        console.print(
            "Instale PDFium: python -m pip install pypdfium2; o Ghostscript desde https://ghostscript.com/releases/gsdnld.html"
        )
    if config.tipo_salida == "pdfa" and not gs:
        console.print("Para PDF/A instale Ghostscript: https://ghostscript.com/releases/gsdnld.html")
        ok = False
    try:
        if not config.entrada.is_dir():
            raise OSError(f"No existe la entrada: {config.entrada}")
        next(config.entrada.iterdir(), None)
        for folder in (config.salida, config.texto, config.errores, config.logs):
            folder.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=folder) as f:
                f.write(b"test")
                f.flush()
            console.print(f"Escritura OK: {folder}; libres: {shutil.disk_usage(folder).free / 2**30:.1f} GiB")
        console.print("Lectura de entrada OK. Carpetas separadas OK.")
    except OSError as exc:
        console.print(f"Problema de rutas/permisos: {exc}")
        ok = False
    return 0 if ok else 1


def parser():
    p = argparse.ArgumentParser(description="OCR Masivo Local — originales de solo lectura")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("diagnostico", "inventariar", "procesar", "reintentar", "reporte"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--config", type=Path)
        for path in ("entrada", "salida", "texto", "errores", "logs"):
            cmd.add_argument(f"--{path}", type=Path)
        cmd.add_argument("--idioma")
        cmd.add_argument("--modo", choices=["skip", "redo", "force"])
        cmd.add_argument("--tipo-salida", choices=["pdf", "pdfa"])
        for flag in ("rotacion", "inclinacion", "generar-txt", "reintentar-fallidos"):
            cmd.add_argument(f"--{flag}", action=argparse.BooleanOptionalAction, default=None)
        cmd.add_argument("--workers", type=int)
        cmd.add_argument("--optimizacion", type=int)
        cmd.add_argument("--timeout-pagina", type=float)
        cmd.add_argument("--megapixeles", type=float)
        if name == "reintentar":
            cmd.add_argument("--solo-fallidos", action="store_true", required=True)
        if name == "reporte":
            cmd.add_argument("--formato", choices=["csv"], default="csv")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        overrides = {f.name: getattr(args, f.name, None) for f in fields(Config)}
        config = load_config(args.config, overrides)
        if args.command == "diagnostico":
            return diagnostic(config)
        db = Database(config.logs / "estado.sqlite3")
        if args.command in {"reintentar", "reporte"} and not args.config:
            saved = db.setting("config")
            if saved:
                saved.update({k: v for k, v in overrides.items() if v is not None})
                config = Config(**saved).validate()
        logging.basicConfig(
            filename=config.logs / "ocr_masivo.log",
            level=logging.INFO,
            encoding="utf-8",
            format="%(asctime)s %(levelname)s %(message)s",
            force=True,
        )
        if args.command == "inventariar":
            with batch_lock(config.logs):
                rows = inventory(config, db)
                db.setting("config", config.serialize())
                db.setting("active_ids", [r["id"] for r in rows])
                report(config, db, rows)
            console.print(f"Inventario: {len(rows)} PDF. Registro: {db.path}")
        elif args.command in {"procesar", "reintentar"}:
            rows = run_batch(config, db, only_failed=args.command == "reintentar")
            path, summary = report(config, db, rows)
            console.print(summary)
            console.print(f"Reporte: {path}")
            return 2 if any(r["estado"] in FAILURES for r in rows) else 0
        else:
            path, summary = report(config, db)
            console.print(summary)
            console.print(f"Reporte: {path}")
        return 0
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        console.print(f"[red]No se pudo continuar: {exc}[/red]")
        return 1
    except KeyboardInterrupt:
        console.print("Interrumpido. Puede reanudar con el mismo comando.")
        return 130
