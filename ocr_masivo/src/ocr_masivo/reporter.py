import csv
import io
import json

from .utils import atomic_text

COLUMNS = """archivo_original ruta_relativa hash_original paginas_original estado fecha_inicio fecha_fin
duracion_segundos codigo_salida archivo_resultado hash_resultado paginas_resultado archivo_texto
caracteres_extraidos mensaje_error""".split()


def report(config, db, rows=None):
    if rows is None:
        ids = db.setting("active_ids")
        rows = [r for r in db.rows() if ids is None or r["id"] in set(ids)]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    csv_path = config.logs / "reporte.csv"
    atomic_text(csv_path, "\ufeff" + stream.getvalue())
    pages = sum(r["paginas_original"] for r in rows)
    seconds = sum(r["duracion_segundos"] for r in rows)
    summary = {
        "total_pdf": len(rows),
        "total_paginas": pages,
        "completados": sum(r["estado"] == "completed" for r in rows),
        "omitidos": sum(r["estado"] == "already_completed" for r in rows),
        "firmados": sum(r["estado"] == "signed" for r in rows),
        "cifrados": sum(r["estado"] == "encrypted" for r in rows),
        "fallidos": sum(r["estado"] in {"failed", "validation_failed"} for r in rows),
        "pendientes": sum(r["estado"] in {"pending", "processing"} for r in rows),
        "tiempo_lote_segundos": db.setting("elapsed") or 0,
        "tiempo_documentos_acumulado_segundos": seconds,
        "promedio_segundos_por_pagina": seconds / pages if pages else 0,
        "bytes_originales": sum(r["tamano"] for r in rows),
        "bytes_pdf_resultantes": sum(r.get("tamano_resultado", 0) for r in rows),
    }
    atomic_text(config.logs / "resumen.json", json.dumps(summary, indent=2, ensure_ascii=False))
    return csv_path, summary
