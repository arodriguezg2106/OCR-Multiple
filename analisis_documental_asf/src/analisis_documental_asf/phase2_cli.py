import argparse
import json
import sqlite3
from pathlib import Path

from .phase2 import build, publish


def parser():
    command = argparse.ArgumentParser(description="Extracción estructurada local — Fase 2")
    command.add_argument("--catalogo", type=Path, required=True, help="indice_documental.sqlite3 de fase 1")
    command.add_argument("--salida", type=Path, default=Path("resultados_fase2"))
    return command


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        output = args.salida.resolve()
        execution = build(
            args.catalogo,
            output / "extraccion_fase2.sqlite3",
            progress=lambda value: print(value, flush=True),
        )
        summary = publish(output / "extraccion_fase2.sqlite3", output, execution)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"Informe: {output / 'extraccion_documental.html'}")
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        print(f"No se pudo completar la fase 2: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
