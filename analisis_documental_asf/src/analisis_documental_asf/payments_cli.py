import argparse
import json
import sqlite3
from decimal import Decimal
from pathlib import Path

from .payments import build


def parser():
    command = argparse.ArgumentParser(description="Cruce local de la relación de pagos con evidencia OCR")
    command.add_argument("--relacion", type=Path, required=True, help="Archivo XLSX de la relación de pagos")
    command.add_argument("--extraccion", type=Path, required=True, help="extraccion_fase2.sqlite3")
    command.add_argument("--salida", type=Path, default=Path("resultados_fase3"))
    command.add_argument("--objetivo", type=Decimal, default=Decimal("45436913.58"))
    command.add_argument("--trazabilidad", type=Path, help="trazabilidad.sqlite3 que se ampliará con el cruce")
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        summary = build(
            args.relacion,
            args.extraccion,
            args.salida,
            args.objetivo,
            args.trazabilidad,
            progress=lambda value: print(value, flush=True),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Informe: {args.salida.resolve() / 'conciliacion_relacion_pagos.html'}")
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"No se pudo completar el cruce: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
