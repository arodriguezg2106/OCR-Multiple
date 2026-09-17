import argparse
import json
import sqlite3
from pathlib import Path

from .phase3 import build


def parser():
    command = argparse.ArgumentParser(description="Trazabilidad y conciliación local — Fase 3")
    command.add_argument("--extraccion", type=Path, required=True, help="extraccion_fase2.sqlite3")
    command.add_argument("--salida", type=Path, default=Path("resultados_fase3"))
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        summary = build(args.extraccion, args.salida, progress=lambda value: print(value, flush=True))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Informe: {args.salida.resolve() / 'trazabilidad_documental.html'}")
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"No se pudo completar la fase 3: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
