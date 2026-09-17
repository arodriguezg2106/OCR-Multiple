import argparse
import json
from pathlib import Path

from .builder import build
from .report import publish


def parser():
    command = argparse.ArgumentParser(description="Catálogo local de documentos con OCR")
    command.add_argument("--ocr-db", type=Path, required=True, help="Base estado.sqlite3 de OCR Masivo")
    command.add_argument("--salida", type=Path, default=Path("resultados"))
    command.add_argument("--forzar", action="store_true", help="Volver a leer PDF ya indexados")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        output = args.salida.resolve()
        database, execution = build(
            args.ocr_db,
            output / "indice_documental.sqlite3",
            force=args.forzar,
            progress=lambda value: print(value, flush=True),
        )
        summary = publish(database, output)
        print(json.dumps({"ejecucion": execution, "catalogo": summary}, ensure_ascii=False, indent=2))
        print(f"Informe: {output / 'catalogo_documental.html'}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"No se pudo construir el catálogo: {exc}")
        return 1
