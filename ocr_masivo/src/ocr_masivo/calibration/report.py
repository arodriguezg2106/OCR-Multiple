import argparse
import csv
import html
import io
import json
import re
from pathlib import Path
from urllib.parse import quote

import pymupdf
from PIL import Image

from ..utils import atomic_text
from .engine import save_json, transform_box
from .fields import crop_score, extract_value, read_region
from .quality import check_total, compare_readings, normalize, validate_field
from .runner import verify


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def pdf_region(path, box, kind):
    with pymupdf.open(path) as doc:
        page = doc[0]
        visual = pymupdf.Rect(
            box[0] * page.rect.width,
            box[1] * page.rect.height,
            box[2] * page.rect.width,
            box[3] * page.rect.height,
        )
        region = visual * ~page.rotation_matrix
        raw = page.get_text(clip=region)
    return dict(**extract_value(kind, raw), confidence=None)


def field_record(profile, raw, expected, kind, **metadata):
    normalized = normalize(raw)
    exact = normalized.casefold() == normalize(expected).casefold()
    if exact:
        error = "sin_error"
    elif not normalized:
        error = "omitido"
    elif re.sub(r"\W", "", normalized).casefold() == re.sub(r"\W", "", expected).casefold():
        error = "separadores_o_formato"
    else:
        error = "lectura_incorrecta"
    return dict(
        profile=profile,
        expected=expected,
        raw_value=raw,
        normalized=normalized,
        exact=exact,
        error_type=error,
        kind=kind,
        **metadata,
    )


def build_records(project, root):
    regions = load(root / "regions.json")
    fields = load(root / "referencia_visual.json")["campos"]
    sample = {p["id"]: p for p in load(root / "muestra.json")}
    original_audit = {r["id"]: r for r in load(root / "comparacion_60_campos.json")}
    records = []
    comparison = []
    for idx, (pid, kind, label, expected) in enumerate(fields, 1):
        page = sample[pid]
        box = regions["boxes"][str(idx)]
        orientation = load(root / "orientation" / pid / "decision.json")
        angle = orientation["angle"]
        mapped = transform_box(box, angle - regions["reference_angles"][pid])
        # A is confirmed by equality of the complete text, not by a selective match to the reference.
        a = load(root / "A" / pid / "reading.json")
        prior = (root.parent / "auditoria_precision" / f"{pid}_ocr.txt").read_text(encoding="utf-8")
        if re.sub(r"\s+", " ", a["text"]).strip() != re.sub(r"\s+", " ", prior).strip():
            raise ValueError(f"A no reproduce el texto de la línea base: {pid}")
        aval = dict(
            value=original_audit[idx]["valor_ocr"],
            raw_region=prior,
            confidence=None,
            method="Texto completo reproducido; valoración por campo de la auditoría congelada",
        )
        bval = pdf_region(root / "B" / pid / "result.pdf", mapped, kind)
        bval["method"] = "Texto PDF en región fija; geometría de caracteres"
        c = load(root / "C" / pid / "reading.json")
        cval = read_region(c, mapped, kind)
        cval["method"] = "TSV de página, PSM 3, best 300 DPI; región fija"
        d_selection = load(root / "D" / pid / "selection.json")
        page_choice = d_selection["chosen"]
        page_base = f"{page_choice['preprocess']}_psm{page_choice['psm']}"
        d_page = load(root / "D" / pid / (page_base + ".json"))
        dpageval = read_region(d_page, mapped, kind)
        # Same confidence/format scoring as crops, without using the expected value.
        page_score, _, _ = crop_score(dict(text=dpageval["raw_region"], words=dpageval["words"]), kind)
        crop_selection = load(root / "D_fields" / f"{idx:02d}" / "selection.json")
        crop = crop_selection["chosen"]
        if page_score > crop["score"]:
            dval = {
                **dpageval,
                "psm": page_choice["psm"],
                "preprocess": page_choice["preprocess"],
                "method": "Página especializada seleccionada por confianza/formato",
                "source": "page",
            }
        else:
            dval = {
                **crop,
                "method": "Región segmentada seleccionada por confianza/formato",
                "source": "crop",
            }
        dval["alternatives_note"] = dict(
            page_raw=dpageval["raw_region"],
            page_value=dpageval["value"],
            page_score=page_score,
            crop_raw=crop["raw_region"],
            crop_value=crop["value"],
            crop_score=crop["score"],
        )
        comparison.append(dict(id=idx, page=pid, **dval["alternatives_note"]))
        vals = {"A": aval, "B": bval, "C": cval, "D": dval}
        # The record contains the unchanged reference and all stages of the observed value.
        for profile, v in vals.items():
            paired = cval if profile == "D" else dval
            comparison_result = compare_readings(kind, v["value"], paired["value"], v["confidence"])
            flags = validate_field(kind, v["value"], v["confidence"])
            if profile != "A" and orientation["ambiguous"]:
                flags.append("orientacion_ambigua")
            row = field_record(
                profile,
                v["value"],
                expected,
                kind,
                id=idx,
                page_id=pid,
                document=page["archivo"],
                page=page["pagina"],
                label=label,
                raw_region=v["raw_region"],
                confidence=v["confidence"],
                orientation=0 if profile == "A" else angle,
                psm=v.get("psm", 3),
                preprocessing=v.get("preprocess", "original" if profile in {"A", "B"} else "gray 1.15"),
                method=v["method"],
                validators=flags,
                comparison=comparison_result,
                requires_review=comparison_result["requiere_revision"] or bool(flags),
                evidence=f"evidence/f{idx:02d}.png",
                selection_details=v.get("alternatives_note"),
            )
            records.append(row)
        original_image = root / "evidence" / f"{pid}.png"
        with Image.open(original_image) as im:
            pixels = [int(v * (im.width if i % 2 == 0 else im.height)) for i, v in enumerate(box)]
            im.crop(pixels).save(root / "evidence" / f"f{idx:02d}.png")
    # Explicit row identities, taken from the original table layout. Totals are not inferred from arbitrary numbers.
    subtotal_constraints = [
        (28, 27, [0.759, 0.186, 0.820, 0.203]),
        (48, 47, [0.759, 0.176, 0.822, 0.194]),
        (53, 52, [0.758, 0.176, 0.823, 0.195]),
    ]
    # Read the second debit row independently from the same already processed page.
    # It is auxiliary control data, not an added benchmark field or expected value.
    totals = []
    for profile in ("A", "B", "C", "D"):
        lookup = {r["id"]: r for r in records if r["profile"] == profile}
        for total, first, secondbox in subtotal_constraints:
            row = lookup[first]
            pid = row["page_id"]
            mapped = transform_box(secondbox, row["orientation"] - regions["reference_angles"][pid])
            if profile in {"A", "B"}:
                second = pdf_region(root / profile / pid / "result.pdf", mapped, "importe")
            elif profile == "C":
                second = read_region(load(root / "C" / pid / "reading.json"), mapped, "importe")
            else:
                choice = load(root / "D" / pid / "selection.json")["chosen"]
                second = read_region(
                    load(root / "D" / pid / f"{choice['preprocess']}_psm{choice['psm']}.json"),
                    mapped,
                    "importe",
                )
            consistent = check_total(lookup[total]["raw_value"], [row["raw_value"], second["value"]])
            totals.append(
                dict(
                    profile=profile,
                    total_field=total,
                    first_component_field=first,
                    second_component_raw=second["raw_region"],
                    second_component_value=second["value"],
                    consistent=consistent,
                    assumption="Primer y segundo renglón de Debe en las tablas de póliza; sin valor esperado para el dato auxiliar.",
                )
            )
            if consistent is False:
                for field in {total, first}:
                    lookup[field]["validators"].append("total_inconsistente_control_parcial")
                    lookup[field]["requires_review"] = True
    save_json(root / "comparacion_lecturas.json", comparison)
    save_json(root / "controles_totales.json", totals)
    return records


def timings(root):
    orientation = sum(load(p)["elapsed"] for p in (root / "orientation").glob("*/decision.json"))
    result = {}
    for profile in ("A", "B", "C", "D"):
        if profile in {"A", "B"}:
            cost = sum(load(p)["elapsed"] for p in (root / profile).glob("*/reading.json"))
        elif profile == "C":
            cost = sum(load(p)["elapsed"] for p in (root / "C").glob("*/reading.json"))
        else:
            paths = list((root / "D").glob("*/*_psm*.json")) + list((root / "D_fields").glob("*/*_psm*.json"))
            cost = sum(load(p)["elapsed"] for p in paths)
        if profile != "A":
            cost += orientation
        timing = root / f"timing_{profile}.json"
        result[profile] = dict(
            total_seconds=round(cost, 2),
            seconds_per_page=round(cost / 12, 2),
            wall_stage_seconds=load(timing)["wall_seconds"] if timing.exists() else None,
            definition="Suma de duración de ejecuciones únicas, incluyendo alternativas y orientación compartida; no es tiempo de pared.",
        )
    return result


def summarize(records, times, oriented):
    groups = []
    for profile in ("A", "B", "C", "D"):
        rows = [r for r in records if r["profile"] == profile]
        exact = sum(r["exact"] for r in rows)
        metrics = dict(
            profile=profile,
            total_exact=exact,
            total_fields=len(rows),
            total_percent=round(100 * exact / len(rows), 2),
            categories={},
            analytical_exact=sum(r["exact"] for r in rows if r["page_id"] in {"p03", "p04"}),
            oriented_pages=oriented[profile],
            detected_by_validators=sum(not r["exact"] and bool(r["validators"]) for r in rows),
            detected_by_any_check=sum(not r["exact"] and r["requires_review"] for r in rows),
            unnoticed_errors=sum(not r["exact"] and not r["requires_review"] for r in rows),
            review_fields=sum(r["requires_review"] for r in rows),
            **times[profile],
        )
        for kind in ("importe", "fecha", "cuenta", "folio"):
            selected = [r for r in rows if r["kind"] == kind]
            n = sum(r["exact"] for r in selected)
            metrics["categories"][kind] = dict(
                exact=n, total=len(selected), percent=round(100 * n / len(selected), 2)
            )
        metrics["promising"] = (
            metrics["total_percent"] >= 85
            and metrics["categories"]["importe"]["percent"] >= 80
            and metrics["analytical_exact"] >= 9
        )
        groups.append(metrics)
    baseline = groups[0]
    for group in groups:
        group["delta_total_pp"] = round(group["total_percent"] - baseline["total_percent"], 2)
        group["delta_categories_pp"] = {
            kind: round(value["percent"] - baseline["categories"][kind]["percent"], 2)
            for kind, value in group["categories"].items()
        }
    # Declared prioritization, rather than total percentage alone.
    recommended = max(
        groups,
        key=lambda g: (
            tuple(g["categories"][k]["percent"] for k in ("importe", "cuenta", "folio", "fecha"))
            + (-g["total_seconds"],)
        ),
    )["profile"]
    return dict(
        profiles=groups,
        recommended=recommended,
        baseline_total=39,
        baseline_fields=60,
        baseline_percent=65.0,
        reference_scope="Muestra dirigida fija de 12 páginas, 60 campos; no generalizable.",
        no_automatic_accounting=True,
        no_mass_processing=True,
    )


def write_csv(path, rows):
    if not rows:
        atomic_text(path, "\ufeffsin_registros\n")
        return
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        writer.writerow(
            {
                k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                for k, v in row.items()
            }
        )
    atomic_text(path, "\ufeff" + stream.getvalue())


def publish(root, records, summary, orientation_rows):
    save_json(root / "resultados_detallados.json", records)
    save_json(root / "resumen.json", summary)
    write_csv(root / "resultados_detallados.csv", records)
    write_csv(root / "comparacion_perfiles.csv", summary["profiles"])
    write_csv(root / "orientaciones_detectadas.csv", orientation_rows)
    write_csv(
        root / "errores_no_resueltos.csv", [r for r in records if not r["exact"] or r["requires_review"]]
    )

    def esc(value):
        return html.escape(str(value))

    table = "<table><tr><th>Perfil</th><th>Total</th><th>Importes</th><th>Fechas</th><th>Cuentas</th><th>Folios</th><th>Estado analítico</th><th>Tiempo acumulado</th></tr>"
    table += "<tr><td>Línea base conservada</td><td>39/60 · 65.0%</td><td>12/23</td><td>11/13</td><td>6/10</td><td>10/14</td><td>0/10</td><td>Auditoría anterior</td></tr>"
    for group in summary["profiles"]:
        table += (
            f"<tr><td>{group['profile']}</td><td>{group['total_exact']}/60 · {group['total_percent']}%</td>"
        )
        for kind in ("importe", "fecha", "cuenta", "folio"):
            c = group["categories"][kind]
            table += f"<td>{c['exact']}/{c['total']} · {c['percent']}%</td>"
        table += f"<td>{group['analytical_exact']}/10</td><td>{group['total_seconds'] / 60:.1f} min</td></tr>"
    table += "</table>"
    cards = []
    ids = sorted({r["id"] for r in records})
    for field in ids:
        variants = [r for r in records if r["id"] == field]
        first = variants[0]
        source = quote("../../Prueba/" + first["document"]) + f"#page={first['page']}"
        markup = f"<article><h2>#{field} · {esc(first['label'])}</h2><p>{esc(first['document'])} · página {first['page']} · {first['kind']}</p>"
        markup += f'<p>Esperado congelado: <strong>{esc(first["expected"])}</strong> · <a href="{source}" target="_blank">PDF original</a></p>'
        markup += f'<a href="{first["evidence"]}" target="_blank"><img src="{first["evidence"]}" alt="Recorte del campo original"></a>'
        markup += "<table><tr><th>Perfil / orientación / PSM</th><th>Lectura cruda del valor</th><th>Normalizado</th><th>Exacto</th><th>Confianza TSV</th><th>Estado y revisión</th></tr>"
        for row in variants:
            markup += f"<tr><td>{row['profile']} · {row['orientation']}° · {row['psm']}</td><td>{esc(row['raw_value']) or '∅'}</td><td>{esc(row['normalized']) or '∅'}</td>"
            markup += f"<td>{'Sí' if row['exact'] else 'No'}</td><td>{'No disponible' if row['confidence'] is None else round(row['confidence'], 1)}</td>"
            markup += f"<td>{row['error_type']}<br>{row['comparison']['classification']}<br>{esc(', '.join(row['validators']))}</td></tr>"
        markup += "</table><details><summary>Lecturas de región completas, métodos y discrepancias</summary>"
        for row in variants:
            markup += f"<h3>Perfil {row['profile']}</h3><p>{esc(row['method'])}</p><pre>{esc(row['raw_region'])}</pre>"
            markup += f"<pre>{esc(json.dumps(row['comparison'], ensure_ascii=False, indent=2))}</pre>"
        markup += "</details></article>"
        cards.append(markup)
    orientation_table = "<table><tr><th>Página</th><th>Rotación PDF original</th><th>Elegida</th><th>Confianza</th><th>Ambigua</th><th>Puntuaciones</th></tr>"
    for row in orientation_rows:
        orientation_table += f"<tr><td>{row['page_id']}</td><td>{row['original_pdf_rotation']}°</td><td>{row['chosen']}°</td><td>{row['confidence']}</td><td>{row['ambiguous']}</td><td>{esc(row['scores'])}</td></tr>"
    orientation_table += "</table>"
    recommended = summary["recommended"]
    promising = any(g["promising"] for g in summary["profiles"])
    conclusion = (
        f"Perfil prioritario en esta calibración: {recommended}, ordenando importes, cuentas, folios, fechas y coste. "
        + (
            "Al menos un perfil alcanza los umbrales de continuación."
            if promising
            else "Ningún perfil alcanza todos los umbrales de continuación (85% total, 80% importes y al menos 9/10 del estado analítico)."
        )
    )
    doc = """<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Calibración OCR v2</title><style>body{font:16px system-ui;color:#182c40;background:#eef2f6;max-width:1400px;margin:auto;padding:24px}
header,section,article{background:white;padding:24px;border-radius:12px;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:10px;border-bottom:1px solid #ced6df;vertical-align:top}img{max-width:100%;max-height:140px;object-fit:contain}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f8;padding:12px}a{color:#165c9d}h1{font-size:28px}h2{font-size:20px}
.note{background:#fff0d0;padding:16px}input{padding:12px;width:90%;font:inherit}article{overflow:auto}[hidden]{display:none}</style>
<header><h1>Segunda calibración controlada de OCR</h1><p>12 páginas · 60 campos · referencias originales congeladas</p>"""
    doc += (
        f'<p class="note">{conclusion} No autoriza extracción contable automática ni procesamiento masivo.</p>'
        + table
    )
    doc += """<p>Tiempo: suma de segundos de todas las ejecuciones únicas del perfil, incluidas alternativas y orientación. No equivale a tiempo de pared con paralelismo.
La orientación es un coste compartido reutilizable entre B/C/D. La confianza TSV es una señal del motor, no una probabilidad de acierto.</p></header>
<section><h2>Qué se ejecutó</h2><ul><li>A: OCRmyPDF con los argumentos originales; se confirmó igualdad del texto completo de las 12 páginas y se conserva la evaluación original.</li>
<li>B: mismo OCRmyPDF, con rotación robusta aplicada únicamente a copias derivadas.</li><li>C: Tesseract español tessdata_best, 300 DPI, gris y contraste 1.15, PSM 3.</li>
<li>D: best, 400 DPI, gris/contraste 1.15; PSM 3/4/6/11 en páginas y regiones. Adaptativa solo si su puntuación ciega mejora más del 2%. Se conservan todas las variantes.</li></ul>
<p>Se detectaron regiones tabulares mediante morfología; no se borraron líneas. La segmentación de los 60 campos utiliza regiones anotadas visualmente en las mismas imágenes, no valores esperados.
Esto es calibración con regiones conocidas, no un extractor automático de tablas desconocidas.</p>
<p>La orientación puntúa confianza, caracteres, patrones, vocabulario, encabezados y geometría horizontal. Se guarda OSD como señal adicional.
La primera decisión exploratoria sin geometría se archivó; no se utiliza en las métricas finales.</p>
<p>Normalización: espacios de maquetación y símbolo $ literal. La localización de tokens excluye signos delimitadores externos, conservando la región cruda completa.
No se sustituyen letras por números, no se rellenan ceros ni se ajustan importes al valor esperado.</p>
<p>Los controles de formatos no pueden detectar por sí solos una cifra equivocada que siga siendo numéricamente válida. Se comparan C y D; para A/B se usa D como contraste.
Los controles de suma son parciales y se identifican como tales, no representan una conciliación contable.</p></section>"""
    doc += "<section><h2>Orientación</h2>" + orientation_table + "</section>"
    doc += "<section><h2>Detección de errores y coste</h2><table><tr><th>Perfil</th><th>Errores señalados / errores reales</th><th>No señalados</th><th>Campos para revisión</th><th>Segundos por página</th></tr>"
    for group in summary["profiles"]:
        doc += f"<tr><td>{group['profile']}</td><td>{group['detected_by_any_check']}/{group['total_fields'] - group['total_exact']}</td><td>{group['unnoticed_errors']}</td><td>{group['review_fields']}</td><td>{group['seconds_per_page']}</td></tr>"
    doc += "</table><p>Las señales incluyen discrepancias entre motores; detectar un error no significa corregirlo. La revisión también incluye campos correctos marcados preventivamente.</p></section>"
    doc += """<section><h2>Recomendación y siguiente experimento</h2>
<p>PDF buscable: usar B como candidato para pruebas supervisadas por su mejor lectura de importes y orientación. La configuración del procesamiento general no se modifica.</p>
<p>Tablas: conservar coordenadas y recortes de evidencia, separando filas y columnas; D no supera a B y no justifica todavía automatizar la extracción tabular.</p>
<p>Extracción numérica: mantener revisión humana de importes, cuentas y folios. No publicar asientos automáticamente. Ningún perfil cumple todos los umbrales.</p>
<p>Siguiente experimento mínimo: sobre estas mismas 12 páginas y 60 referencias congeladas, probar segmentación por celdas y márgenes ajustados a líneas de texto sobre B, con una segunda lectura numérica independiente y rechazo explícito cuando discrepen. Mantener ceros, separadores y texto crudo; comparar los 60 campos completos sin elegir variantes por el valor esperado. Después deberá validarse con otra muestra independiente.</p></section>"""
    doc += (
        "<section><h2>Archivos de auditoría</h2><p>"
        + " · ".join(
            f'<a href="{name}">{name}</a>'
            for name in (
                "resultados_detallados.csv",
                "comparacion_perfiles.csv",
                "orientaciones_detectadas.csv",
                "errores_no_resueltos.csv",
                "resumen.json",
            )
        )
        + "</p>"
    )
    doc += '<input id="search" type="search" placeholder="Filtrar por campo, documento, valor o incidencia" aria-label="Filtrar campos"></section>'
    doc += "\n".join(cards)
    doc += """<script>document.querySelector('#search').addEventListener('input',e=>{const q=e.target.value.toLocaleLowerCase();document.querySelectorAll('article').forEach(a=>a.hidden=!a.textContent.toLocaleLowerCase().includes(q))});</script></html>"""
    atomic_text(root / "informe_precision_v2.html", doc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=Path.cwd())
    args = parser.parse_args()
    project = args.project.resolve()
    root = project / "prueba_resultados/auditoria_precision_v2"
    verify(root)
    records = build_records(project, root)
    for row in records:
        profile, pid = row["profile"], row["page_id"]
        if profile == "D":
            paths = list((root / "D" / pid).glob("*_psm*.json"))
            paths += list((root / "D_fields" / f"{row['id']:02d}").glob("*_psm*.json"))
            seconds = sum(load(p)["elapsed"] for p in paths)
        else:
            seconds = load(root / profile / pid / "reading.json")["elapsed"]
        if profile != "A":
            seconds += load(root / "orientation" / pid / "decision.json")["elapsed"]
        row["processing_seconds"] = round(seconds, 2)
        row["timing_scope"] = (
            "Lectura de página y orientación compartidas; D añade variantes del campo. No sumar filas."
        )
    regions = load(root / "regions.json")
    orientations = []
    for pid, expected_angle in regions["reference_angles"].items():
        d = load(root / "orientation" / pid / "decision.json")
        orientations.append(
            dict(
                page_id=pid,
                original_pdf_rotation=d["original_pdf_rotation"],
                chosen=d["angle"],
                reference_angle=expected_angle,
                correct=d["angle"] == expected_angle,
                confidence=d["confidence"],
                ambiguous=d["ambiguous"],
                method=d["method"],
                scores={a["angle"]: a for a in d["alternatives"]},
                osd=d["osd"],
            )
        )
    correct = sum(o["correct"] for o in orientations)
    summary = summarize(records, timings(root), dict(A=10, B=correct, C=correct, D=correct))
    summary["orientations"] = orientations
    publish(root, records, summary, orientations)
    verify(root)
    print(json.dumps({k: v for k, v in summary.items() if k != "orientations"}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
