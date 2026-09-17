import csv
import html
import io
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".catalog-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def decode(record):
    result = dict(record)
    for field in ("contained_types", "classification_evidence", "detected_dates", "frequent_terms"):
        result[field] = json.loads(result[field])
    return result


def csv_text(rows):
    if not rows:
        return "\ufeffsin_registros\n"
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + output.getvalue()


def flat_document(row):
    return {
        "id": row["id"],
        "mes_numero": row["month_number"] or "",
        "carpeta_mes": row["month_folder"],
        "ruta_relativa": row["relative_path"],
        "nombre": row["filename"],
        "estado_ocr": row["ocr_status"],
        "paginas": row["pages"],
        "paginas_con_texto": row["pages_with_text"],
        "caracteres": row["text_characters"],
        "cobertura_texto": row["coverage"],
        "tipo_principal": row["primary_type"],
        "confianza_clasificacion": row["classification_confidence"],
        "tipos_contenidos": " | ".join(row["contained_types"]),
        "fecha_minima_detectada": row["date_min"] or "",
        "fecha_maxima_detectada": row["date_max"] or "",
        "fechas_unicas_detectadas": len(row["detected_dates"]),
        "terminos_frecuentes": " | ".join(item["term"] for item in row["frequent_terms"]),
        "advertencia": row["warning"].replace("\r", " ").replace("\n", " "),
        "ruta_pdf_ocr": row["pdf_path"],
        "ruta_original": row["original_path"],
    }


def summaries(rows):
    months = defaultdict(Counter)
    types = defaultdict(Counter)
    for row in rows:
        month = months[row["month_folder"]]
        month["mes_numero"] = row["month_number"] or 0
        month["documentos"] += 1
        month["paginas"] += row["pages"]
        month["caracteres"] += row["text_characters"]
        month["ocr_completado"] += row["ocr_status"] in {"completed", "already_completed"}
        month["requiere_revision"] += row["classification_confidence"] == "baja" or bool(row["warning"])
        kind = types[row["primary_type"]]
        kind["documentos"] += 1
        kind["paginas"] += row["pages"]
        kind["confianza_alta"] += row["classification_confidence"] == "alta"
        kind["confianza_media"] += row["classification_confidence"] == "media"
        kind["confianza_baja"] += row["classification_confidence"] == "baja"
    month_rows = [
        {
            "mes_numero": data["mes_numero"],
            "carpeta_mes": name,
            **{
                k: data[k]
                for k in ("documentos", "paginas", "caracteres", "ocr_completado", "requiere_revision")
            },
        }
        for name, data in sorted(months.items(), key=lambda item: (item[1]["mes_numero"], item[0]))
    ]
    type_rows = [
        {
            "tipo_principal": name,
            **{
                k: data[k]
                for k in ("documentos", "paginas", "confianza_alta", "confianza_media", "confianza_baja")
            },
        }
        for name, data in sorted(types.items(), key=lambda item: (-item[1]["documentos"], item[0]))
    ]
    return month_rows, type_rows


def local_link(path):
    try:
        return Path(path).resolve().as_uri() if path else ""
    except ValueError:
        return ""


def build_html(rows, month_rows, type_rows, database_name):
    total_pages = sum(row["pages"] for row in rows)
    completed = sum(row["ocr_status"] in {"completed", "already_completed"} for row in rows)
    review = sum(row["classification_confidence"] == "baja" or bool(row["warning"]) for row in rows)
    month_options = "".join(
        f'<option value="{html.escape(row["carpeta_mes"])}">{html.escape(row["carpeta_mes"])}</option>'
        for row in month_rows
    )
    type_options = "".join(
        f'<option value="{html.escape(row["tipo_principal"])}">{html.escape(row["tipo_principal"])}</option>'
        for row in type_rows
    )
    month_table = "".join(
        f"<tr><td>{html.escape(row['carpeta_mes'])}</td><td>{row['documentos']}</td><td>{row['paginas']}</td><td>{row['ocr_completado']}</td><td>{row['requiere_revision']}</td></tr>"
        for row in month_rows
    )
    type_table = "".join(
        f"<tr><td>{html.escape(row['tipo_principal'])}</td><td>{row['documentos']}</td><td>{row['paginas']}</td><td>{row['confianza_alta']}</td><td>{row['confianza_media']}</td><td>{row['confianza_baja']}</td></tr>"
        for row in type_rows
    )
    documents = []
    for row in rows:
        terms = ", ".join(item["term"] for item in row["frequent_terms"][:8])
        types = ", ".join(row["contained_types"]) or "Sin señales suficientes"
        evidence = html.escape(json.dumps(row["classification_evidence"], ensure_ascii=False, indent=2))
        warning = f'<p class="warning">{html.escape(row["warning"])}</p>' if row["warning"] else ""
        pdf_link = local_link(row["pdf_path"])
        original_link = local_link(row["original_path"])
        documents.append(
            f'<article data-month="{html.escape(row["month_folder"])}" data-type="{html.escape(row["primary_type"])}">'
            f'<h3>{html.escape(row["filename"])}</h3><p class="path">{html.escape(row["relative_path"])}</p>'
            f"<p><span>{html.escape(row['primary_type'])}</span> · confianza {row['classification_confidence']} · "
            f"{row['pages']} páginas · cobertura {row['coverage']}</p>"
            f"<p>Contenido detectado: {html.escape(types)}<br>Fechas: {row['date_min'] or '—'} a {row['date_max'] or '—'}<br>"
            f"Términos: {html.escape(terms) or '—'}</p>{warning}"
            f'<p><a href="{pdf_link}">PDF con OCR</a> · <a href="{original_link}">Original</a></p>'
            f"<details><summary>Evidencia de clasificación</summary><pre>{evidence}</pre></details></article>"
        )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Catálogo documental ASF</title><style>
body{{font:15px system-ui;background:#eef2f5;color:#172b3a;max-width:1500px;margin:auto;padding:24px}}header,section,article{{background:white;border-radius:12px;padding:20px;margin:14px 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}.card{{background:#173f5f;color:white;padding:18px;border-radius:10px;font-size:20px}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border-bottom:1px solid #d8e0e6;text-align:left}}.controls{{display:flex;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:2}}
input,select{{padding:11px;font:inherit;min-width:220px}}article span{{background:#dcebf7;padding:4px 8px;border-radius:12px}}.warning{{background:#fff0d0;padding:10px}}.path{{color:#50697b}}
pre{{white-space:pre-wrap;background:#f4f6f8;padding:10px}}a{{color:#075c9d}}[hidden]{{display:none}}</style>
<header><h1>Catálogo documental — Fase 1</h1><p>Inventario local explicable. La clasificación automática requiere validación; no constituye conclusión contable.</p>
<div class="cards"><div class="card">{len(rows)}<small><br>PDF catalogados</small></div><div class="card">{total_pages}<small><br>páginas</small></div><div class="card">{completed}<small><br>OCR completados</small></div><div class="card">{review}<small><br>por revisar</small></div></div>
<p>Índice de texto: <code>{html.escape(database_name)}</code>. Los datos y rutas permanecen en este equipo.</p></header>
<section><h2>Resumen por mes</h2><table><tr><th>Carpeta</th><th>Documentos</th><th>Páginas</th><th>OCR completo</th><th>Revisión</th></tr>{month_table}</table></section>
<section><h2>Resumen por tipo principal</h2><table><tr><th>Tipo</th><th>Documentos</th><th>Páginas</th><th>Alta</th><th>Media</th><th>Baja</th></tr>{type_table}</table></section>
<section class="controls"><input id="search" type="search" placeholder="Buscar nombre, ruta, tipo o término"><select id="month"><option value="">Todos los meses</option>{month_options}</select><select id="type"><option value="">Todos los tipos</option>{type_options}</select><strong id="count"></strong></section>
<main>{"".join(documents)}</main><script>
const cards=[...document.querySelectorAll('article')], search=document.querySelector('#search'), month=document.querySelector('#month'), type=document.querySelector('#type'), count=document.querySelector('#count');
function filter(){{let q=search.value.toLocaleLowerCase(),n=0;cards.forEach(c=>{{let show=(!q||c.textContent.toLocaleLowerCase().includes(q))&&(!month.value||c.dataset.month===month.value)&&(!type.value||c.dataset.type===type.value);c.hidden=!show;if(show)n++}});count.textContent=n+' documentos visibles'}}
[search,month,type].forEach(e=>e.addEventListener('input',filter));filter();</script></html>"""


def publish(database, output_dir):
    output_dir = Path(output_dir).resolve()
    rows = [decode(row) for row in database.documents()]
    month_rows, type_rows = summaries(rows)
    flat = [flat_document(row) for row in rows]
    review = [row for row in flat if row["confianza_clasificacion"] == "baja"]
    warnings = [row for row in flat if row["advertencia"]]
    atomic_text(output_dir / "catalogo_documentos.csv", csv_text(flat))
    atomic_text(output_dir / "resumen_por_mes.csv", csv_text(month_rows))
    atomic_text(output_dir / "resumen_por_tipo.csv", csv_text(type_rows))
    atomic_text(output_dir / "documentos_sin_clasificar.csv", csv_text(review))
    atomic_text(output_dir / "documentos_con_advertencias.csv", csv_text(warnings))
    summary = {
        "documents": len(rows),
        "pages": sum(row["pages"] for row in rows),
        "ocr_completed": sum(row["ocr_status"] in {"completed", "already_completed"} for row in rows),
        "requires_classification_review": len(review),
        "warnings": len(warnings),
        "by_primary_type": {row["tipo_principal"]: row["documentos"] for row in type_rows},
    }
    atomic_text(output_dir / "resumen.json", json.dumps(summary, ensure_ascii=False, indent=2))
    atomic_text(
        output_dir / "catalogo_documental.html",
        build_html(rows, month_rows, type_rows, database.path.name),
    )
    return summary
