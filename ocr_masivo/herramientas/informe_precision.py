"""Publica una evaluación reproducible a partir de transcripción y revisión visual explícitas."""

import argparse
import csv
import hashlib
import html
import io
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote

from ocr_masivo.database import Database
from ocr_masivo.utils import atomic_text, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directorio", type=Path)
    root = parser.parse_args().directorio.resolve()
    reference = root / "referencia_visual.json"
    assert (
        hashlib.sha256(reference.read_bytes()).hexdigest()
        == (root / "referencia_visual.sha256").read_text().strip()
    )
    fields = json.loads(reference.read_text(encoding="utf-8"))["campos"]
    reviewed = json.loads((root / "revision_campos.json").read_text(encoding="utf-8"))
    pages = {p["id"]: p for p in json.loads((root / "muestra.json").read_text(encoding="utf-8"))}
    good = set(reviewed["correctos_revisados"])
    failures = {int(k): v for k, v in reviewed["incidencias"].items()}
    assert not good.intersection(failures)
    assert good.union(failures) == set(range(1, len(fields) + 1))
    db = Database(root.parent / "logs" / "estado.sqlite3")
    documents = {r["ruta_relativa"]: r for r in db.rows()}
    rows = []
    for number, (pageid, category, label, expected) in enumerate(fields, 1):
        page = pages[pageid]
        doc = documents[page["archivo"]]
        assert sha256(doc["archivo_original"]) == doc["hash_original"]
        assert sha256(doc["archivo_resultado"]) == doc["hash_resultado"]
        text = (root / f"{pageid}_ocr.txt").read_text(encoding="utf-8")
        if number in good:
            # This is a consistency check, not an automatic replacement for the field's visual review.
            pattern = r"(?<![\w])" + r"\s+".join(re.escape(s) for s in expected.split()) + r"(?![\w])"
            assert re.search(pattern, text, re.IGNORECASE), f"Campo positivo sin evidencia textual: {number}"
            observed, state, note = expected, "exacto", "Valor contrastado en el campo indicado."
        else:
            result = failures[number]
            observed, state, note = result["ocr"], result["estado"], result["nota"]
            if observed:
                assert observed in text, f"Incidencia sin fragmento OCR: {number}"
        rows.append(
            dict(
                id=number,
                documento=page["archivo"],
                pagina=page["pagina"],
                tipo=category,
                campo=label,
                valor_original=expected,
                valor_ocr=observed,
                estado=state,
                observacion=note,
                hash_original=doc["hash_original"],
                hash_pdf_ocr=doc["hash_resultado"],
                imagen_referencia=f"{pageid}_lectura.png" if pageid in {"p03", "p04"} else page["imagen"],
                texto_ocr=f"{pageid}_ocr.txt",
            )
        )
    counts = Counter(r["estado"] for r in rows)
    by_type = []
    for category in ("importe", "fecha", "cuenta", "folio"):
        group = [r for r in rows if r["tipo"] == category]
        exact = sum(r["estado"] == "exacto" for r in group)
        by_type.append(
            dict(
                tipo=category, campos=len(group), exactos=exact, porcentaje=round(100 * exact / len(group), 2)
            )
        )
    by_doc = []
    for name in sorted({r["documento"] for r in rows}):
        group = [r for r in rows if r["documento"] == name]
        exact = sum(r["estado"] == "exacto" for r in group)
        by_doc.append(
            dict(
                documento=name,
                campos=len(group),
                exactos=exact,
                porcentaje=round(100 * exact / len(group), 2),
            )
        )
    summary = dict(
        campos=len(rows),
        paginas=len(pages),
        documentos=len(by_doc),
        exactos=counts["exacto"],
        porcentaje_exacto=round(100 * counts["exacto"] / len(rows), 2),
        estados=dict(counts),
        por_tipo=by_type,
        por_documento=by_doc,
        alcance="Muestra dirigida de valores legibles en 12 páginas; no estima precisión de todo el archivo.",
        revisor=reviewed["revisor"],
        criterio=reviewed["criterio"],
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic_text(root / "comparacion_60_campos.csv", "\ufeff" + stream.getvalue())
    atomic_text(root / "comparacion_60_campos.json", json.dumps(rows, ensure_ascii=False, indent=2))
    atomic_text(root / "resumen_precision.json", json.dumps(summary, ensure_ascii=False, indent=2))
    title = f"{counts['exacto']} de {len(rows)} campos exactos ({summary['porcentaje_exacto']:.1f} %)"
    methods = (
        "Muestra dirigida: 60 campos legibles, 12 páginas y seis documentos. Incluye páginas con y sin alertas. "
        "La referencia se transcribió visualmente del original antes de leer el OCR y se conservó su SHA-256. "
        "La revisión fue realizada por el asistente, sin segundo revisor independiente. "
        "No es una muestra aleatoria ni una certificación de precisión del conjunto. "
        "Se evaluó el campo indicado: un importe correcto en otro renglón no compensa un error. "
        "Se ignoran espacios de maquetación y símbolos monetarios separados, pero no se corrigen dígitos, letras, "
        "signos, ceros iniciales ni separadores. No se validaron relaciones contables ni la veracidad de lo impreso."
    )
    lines = [
        "# Evaluación de precisión del OCR",
        "",
        title,
        "",
        methods,
        "",
        "| Tipo | Campos | Exactos | Porcentaje |",
        "|---|---:|---:|---:|",
    ]
    for group in by_type:
        lines.append(
            f"| {group['tipo']} | {group['campos']} | {group['exactos']} | {group['porcentaje']:.1f}% |"
        )
    lines += ["", "| Documento | Campos | Exactos | Porcentaje |", "|---|---:|---:|---:|"]
    for group in by_doc:
        lines.append(
            f"| {group['documento']} | {group['campos']} | {group['exactos']} | {group['porcentaje']:.1f}% |"
        )
    lines += [
        "",
        "## Hallazgos",
        "",
        "- Estado analítico: los diez campos fallaron. Ambas páginas permanecieron giradas y produjeron texto ilegible.",
        "- Balanza, página 1: el Haber 26,150,056.00 quedó como 26,150,. La ausencia de alertas no garantizó exactitud.",
        "- Finiquito 2, página 1: el primer 23,319.00 quedó como 523,319.00. Otro renglón correcto no subsana ese error.",
        "- Finiquito 2, página 3: OP202101000143 quedó como 0P202101000143 (O/cero).",
        "- Finiquito 4, página 4: 45,999.00 quedó como 45,900.00 en el primer renglón.",
        "- Una cuenta conservó sus dígitos pero cambió puntos por dos puntos; se separó como diferencia de formato.",
        "",
        "## Uso recomendado a partir de esta prueba",
        "",
        "El resultado actual ayuda a localizar evidencia, pero no es suficientemente fiable para cargar importes o cuentas sin revisión. "
        "Antes de procesar masivamente conviene corregir la orientación de las páginas afectadas en copias derivadas, "
        "añadir controles de calidad por página y repetir la evaluación. No se modificó el OCR evaluado durante esta medición.",
        "",
        "## Evidencia",
        "",
        "- [Comparación navegable de los 60 campos](informe_precision.html)",
        "- [CSV detallado](comparacion_60_campos.csv)",
        "- [Referencia visual](referencia_visual.json)",
        "- [Revisión y observaciones](revision_campos.json)",
        "",
        "Los enlaces permiten abrir la imagen de referencia y el texto OCR. "
        "Las imágenes del estado analítico se giraron solo para facilitar su lectura en esta revisión; "
        "los PDF originales y sus resultados permanecen intactos. "
        "En Excel importe las columnas de valores como texto para conservar ceros iniciales.",
    ]
    atomic_text(root / "INFORME_PRECISION.md", "\n".join(lines))
    cards = []
    for row in rows:
        source = quote("../../Prueba/" + row["documento"]) + f"#page={row['pagina']}"
        pdf = quote("../pdf/" + row["documento"]) + f"#page={row['pagina']}"
        cards.append(
            f'<article data-estado="{row["estado"]}"><h2>#{row["id"]} · {html.escape(row["campo"])}</h2>'
            f"<p>{html.escape(row['documento'])} · página {row['pagina']} · {row['tipo']}</p>"
            f'<p class="status">{row["estado"]}</p><div class="values"><p>Original<br><strong>'
            f"{html.escape(row['valor_original'])}</strong></p><p>OCR<br><strong>"
            f"{html.escape(row['valor_ocr']) or 'No recuperado como valor legible'}</strong></p></div>"
            f'<p>{html.escape(row["observacion"])}</p><nav><a href="{row["imagen_referencia"]}" target="_blank">Imagen original</a>'
            f' · <a href="{source}" target="_blank">PDF original</a> · <a href="{pdf}" target="_blank">PDF OCR</a>'
            f' · <a href="{row["texto_ocr"]}" target="_blank">Texto de la página</a></nav></article>'
        )
    table = (
        "<table><tr><th>Tipo</th><th>Campos</th><th>Exactos</th><th>%</th></tr>"
        + "".join(
            f"<tr><td>{g['tipo']}</td><td>{g['campos']}</td><td>{g['exactos']}</td><td>{g['porcentaje']:.1f}</td></tr>"
            for g in by_type
        )
        + "</table>"
    )
    document = (
        """<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evaluación de precisión OCR</title><style>body{font:16px system-ui;color:#19283b;background:#f0f3f7;max-width:1100px;margin:30px auto;padding:20px}
header,article{background:white;border-radius:10px;padding:22px;margin-bottom:16px}h1{font-size:26px}h2{font-size:19px}
.values{display:flex;gap:35px;flex-wrap:wrap}article{border-left:5px solid #b35130}article[data-estado=exacto]{border-color:#278458}
.status{font-weight:bold}a{color:#205ca0}table{border-collapse:collapse}td,th{padding:8px 20px;border-bottom:1px solid #ccd5df;text-align:left}
button{padding:10px;margin:10px 10px 0 0;cursor:pointer}[hidden]{display:none}</style><header><h1>"""
        + title
        + "</h1><p>"
        + methods
        + "</p>"
        + table
        + """
<p>Los originales y los PDF OCR evaluados conservan sus hashes. Todo el informe funciona localmente.</p>
<button onclick="filter(false)">Todos los campos</button><button onclick="filter(true)">Solo incidencias</button></header>"""
        + "\n".join(cards)
        + """
<script>function filter(onlyErrors){document.querySelectorAll('article').forEach(a=>a.hidden=onlyErrors&&a.dataset.estado==='exacto')}</script></html>"""
    )
    atomic_text(root / "informe_precision.html", document)
    print(json.dumps(summary, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
