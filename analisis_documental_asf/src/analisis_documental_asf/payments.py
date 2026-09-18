"""Cross-reference an external XLSX payment list with phase 2 evidence."""

import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

XLSX_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CENT = Decimal("0.01")
MONTHS = {
    "ENE": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
}
PAID_TYPES = {"cheque", "lote_transferencia", "movimiento_bancario"}


def fold(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(character for character in value if not unicodedata.combining(character)).upper()


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(raw):
    """Return (amount, status) and recover common thousands/decimal punctuation."""
    value = str(raw or "").strip().replace("$", "").replace(" ", "")
    if not value:
        return None, "vacio"
    try:
        return money(value), "numerico"
    except InvalidOperation:
        pass
    separators = [match.start() for match in re.finditer(r"[.,]", value)]
    if separators and len(value) - separators[-1] - 1 == 2:
        normalized = re.sub(r"[.,]", "", value[: separators[-1]]) + "." + value[separators[-1] + 1 :]
        if re.fullmatch(r"-?\d+\.\d{2}", normalized):
            return money(normalized), "recuperado_separadores"
    return None, "invalido"


def _column(reference):
    return "".join(character for character in reference if character.isalpha())


def read_xlsx(path):
    """Read the first worksheet using only the Python standard library."""
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            strings = [
                "".join(node.text or "" for node in item.iter(f"{{{XLSX_NS}}}t"))
                for item in root.findall(f"{{{XLSX_NS}}}si")
            ]
        xml = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        rows = []
        for node in xml.iter(f"{{{XLSX_NS}}}row"):
            values = {}
            for cell in node.findall(f"{{{XLSX_NS}}}c"):
                column = _column(cell.attrib["r"])
                value_node = cell.find(f"{{{XLSX_NS}}}v")
                value = "" if value_node is None else value_node.text or ""
                if cell.attrib.get("t") == "s" and value:
                    value = strings[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(
                        part.text or "" for part in cell.iter(f"{{{XLSX_NS}}}t")
                    )
                values[column] = value
            rows.append((int(node.attrib["r"]), values))
    if not rows:
        raise ValueError("La relación de pagos no contiene filas.")
    return rows


def payment_rows(path):
    raw_rows = read_xlsx(path)
    headers = {column: fold(value).strip() for column, value in raw_rows[0][1].items()}
    expected = {"A": "CUENTA", "B": "ORIGEN", "D": "MES", "E": "MONTO", "F": "CONCEPTO"}
    if any(headers.get(column) != label for column, label in expected.items()):
        raise ValueError("La primera hoja no tiene las columnas esperadas de la relación de pagos.")
    rows = []
    stated_total = None
    for row_number, values in raw_rows[1:]:
        if fold(values.get("D")) == "MONTO TOTAL":
            stated_total, _ = parse_amount(values.get("E"))
            continue
        if not any(str(value).strip() for value in values.values()):
            continue
        amount, amount_status = parse_amount(values.get("E"))
        rows.append(
            {
                "excel_row": row_number,
                "account": str(values.get("A", "")).strip(),
                "origin": str(values.get("B", "")).strip(),
                "date_text": str(values.get("C", "")).strip(),
                "month": fold(values.get("D")),
                "amount_raw": str(values.get("E", "")).strip(),
                "amount": amount,
                "amount_status": amount_status,
                "concept": str(values.get("F", "")).strip(),
            }
        )
    return rows, stated_total


def _path_month(path):
    match = re.match(r"(\d{1,2})\.", path)
    return int(match.group(1)) if match else 0


def _identifier(value):
    digits = "".join(re.findall(r"\d+", str(value)))
    return digits.lstrip("0") or "0"


def _path_numbers(path):
    return {_identifier(value) for value in re.findall(r"\d+", PurePosixPath(path).stem)}


def _concept_words(value):
    ignored = {"DE", "DEL", "EL", "LA", "POR", "PARA", "MES", "PAGO", "NOMINA"}
    return {word for word in re.findall(r"[A-Z]{4,}", fold(value)) if word not in ignored}


def _document_score(row, document, unique_amount_paths):
    month = MONTHS.get(row["month"][:3], 0)
    if month != document["month"]:
        return 0, []
    identifier = _identifier(row["account"])
    reasons = []
    score = 0
    if fold(row["account"]).startswith("POL"):
        if "POLIZA" not in document["stem"] or identifier not in document["path_numbers"]:
            return 0, []
        score += 100
        reasons.append("poliza_en_nombre_archivo")
    elif identifier in document["path_numbers"]:
        score += 80
        reasons.append("identificador_en_nombre_archivo")
    if identifier in document["folios"] and identifier != "0":
        score += 65
        reasons.append("folio_exacto")
    if identifier != "0" and re.search(
        rf"\b(?:CH|TRANSF|FOLIO)\s*[:#-]?\s*0*{re.escape(identifier)}\b", document["evidence"]
    ):
        score += 55
        reasons.append("identificador_en_evidencia")
    if row["amount"] is not None and row["amount"] in document["amounts"]:
        score += 25
        reasons.append("importe_exacto")
        if unique_amount_paths.get((month, row["amount"])) == {document["path"]}:
            score += 40
            reasons.append("importe_unico_en_mes")
    words = _concept_words(row["concept"])
    if words:
        overlap = len(words & document["concept_words"]) / len(words)
        if overlap >= 0.6:
            score += 10
            reasons.append("concepto_coincidente")
    return score, reasons


def match_rows(rows, records):
    documents = defaultdict(list)
    for record in records:
        documents[record["relative_path"]].append(record)
    document_index = []
    amount_paths = defaultdict(set)
    for path, document_records in documents.items():
        amounts = {
            money(record["amount"])
            for record in document_records
            if record.get("amount")
        }
        document = {
            "path": path,
            "records": document_records,
            "month": _path_month(path),
            "stem": fold(PurePosixPath(path).stem),
            "path_numbers": _path_numbers(path),
            "folios": {_identifier(record.get("folio")) for record in document_records if record.get("folio")},
            "evidence": fold(" ".join(record.get("evidence") or "" for record in document_records)),
            "amounts": amounts,
        }
        document["concept_words"] = _concept_words(document["evidence"])
        document_index.append(document)
        for amount in amounts:
            amount_paths[(document["month"], amount)].add(path)
    results = []
    links = []
    for row in rows:
        ranked = []
        for document in document_index:
            score, reasons = _document_score(row, document, amount_paths)
            if score >= 55:
                ranked.append((score, document["path"], reasons, document["records"]))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        best = ranked[0] if ranked else None
        ambiguous = bool(best and len(ranked) > 1 and ranked[1][0] >= best[0] - 10)
        exact_amount_records = []
        if best and row["amount"] is not None:
            exact_amount_records = [
                record
                for record in best[3]
                if record.get("amount") and money(record["amount"]) == row["amount"]
            ]
        paid_records = [record for record in (best[3] if best else []) if record["record_type"] in PAID_TYPES]
        paid_records.sort(
            key=lambda record: (
                {"lote_transferencia": 3, "cheque": 2, "movimiento_bancario": 1}.get(record["record_type"], 0),
                record["page_number"],
            ),
            reverse=True,
        )
        paid_amounts = sorted(
            {money(record["amount"]) for record in paid_records if record.get("amount")}
        )
        paid_amount = paid_amounts[0] if len(paid_amounts) == 1 else None
        paid_record = next(
            (
                record
                for record in paid_records
                if paid_amount is not None
                and record.get("amount")
                and money(record["amount"]) == paid_amount
            ),
            None,
        )
        amount_text = f"{row['amount']:,.2f}" if row["amount"] is not None else ""
        excel_amount_in_paid_evidence = bool(
            amount_text
            and any(amount_text in (record.get("evidence") or "") for record in paid_records)
        )
        evidence_record = (exact_amount_records or paid_records or (best[3] if best else []))[:1]
        evidence_record = evidence_record[0] if evidence_record else None
        source_amount = money(evidence_record["amount"]) if evidence_record and evidence_record.get("amount") else None
        difference = source_amount - row["amount"] if source_amount is not None and row["amount"] is not None else None
        match_status = "sin_coincidencia"
        confidence = "baja"
        if best:
            match_status = "ambiguo" if ambiguous else "vinculado"
            confidence = "media" if ambiguous or best[0] < 80 else "alta"
        result = {
            **row,
            "match_status": match_status,
            "match_confidence": confidence,
            "match_score": best[0] if best else 0,
            "match_reasons": best[2] if best else [],
            "matched_path": best[1] if best else "",
            "matched_record_key": evidence_record["record_key"] if evidence_record else "",
            "matched_type": evidence_record["record_type"] if evidence_record else "",
            "matched_page": evidence_record["page_number"] if evidence_record else "",
            "source_amount": source_amount,
            "amount_difference": difference,
            "paid_amounts": paid_amounts,
            "paid_amount": paid_amount,
            "paid_page": paid_record["page_number"] if paid_record else "",
            "paid_difference": paid_amount - row["amount"]
            if paid_amount is not None and row["amount"] is not None
            else None,
            "excel_amount_in_paid_evidence": excel_amount_in_paid_evidence,
        }
        results.append(result)
        if best:
            for rank, (score, path, reasons, document_records) in enumerate(ranked[:3], 1):
                representative = next(
                    (record for record in document_records if record["record_key"] == result["matched_record_key"]),
                    document_records[0],
                )
                links.append(
                    {
                        "excel_row": row["excel_row"],
                        "record_key": representative["record_key"],
                        "relative_path": path,
                        "score": score,
                        "rank": rank,
                        "status": "aceptado" if rank == 1 and not ambiguous else "sugerido",
                        "reasons": reasons,
                    }
                )
    return results, links


def duplicate_rows(rows):
    groups = defaultdict(list)
    for row in rows:
        key = (
            fold(row["account"]),
            row["month"],
            row["amount"],
            fold(row["concept"]),
        )
        groups[key].append(row)
    return [group for group in groups.values() if len(group) > 1 and group[0]["amount"] is not None]


def confirmed_findings(rows, matches):
    by_number = {row["excel_row"]: row for row in rows}
    match_by_number = {row["excel_row"]: row for row in matches}
    findings = []
    for row in rows:
        if row["amount_status"] == "recuperado_separadores":
            findings.append(
                {
                    "kind": "importe_como_texto",
                    "excel_rows": str(row["excel_row"]),
                    "account": row["account"],
                    "current_amount": "",
                    "proposed_amount": row["amount"],
                    "adjustment": row["amount"],
                    "confidence": "alta",
                    "affects_confirmed_total": True,
                    "evidence": match_by_number[row["excel_row"]]["matched_path"],
                    "detail": "El valor usa dos puntos y Excel lo excluye de la suma.",
                }
            )
    for group in duplicate_rows(rows):
        first = group[0]
        duplicate_count = len(group) - 1
        finding_match = match_by_number[first["excel_row"]]
        findings.append(
            {
                "kind": "duplicado_probable",
                "excel_rows": ", ".join(str(item["excel_row"]) for item in group),
                "account": first["account"],
                "current_amount": first["amount"] * len(group),
                "proposed_amount": first["amount"],
                "adjustment": -(first["amount"] * duplicate_count),
                "confidence": "alta" if finding_match["match_status"] == "vinculado" else "media",
                "affects_confirmed_total": True,
                "evidence": finding_match["matched_path"],
                "detail": f"{len(group)} filas idénticas; se conserva una operación.",
            }
        )
    # This capture error is corroborated by both cheque and transfer in POLIZA0193.pdf.
    if 395 in by_number and match_by_number.get(395, {}).get("source_amount") == Decimal("111577.00"):
        row = by_number[395]
        findings.append(
            {
                "kind": "importe_incompleto",
                "excel_rows": "395",
                "account": row["account"],
                "current_amount": row["amount"],
                "proposed_amount": Decimal("111577.00"),
                "adjustment": Decimal("100000.00"),
                "confidence": "alta",
                "affects_confirmed_total": True,
                "evidence": match_by_number[395]["matched_path"] + ", páginas 8-9",
                "detail": "Cheque y lote de transferencia muestran $111,577.00.",
            }
        )
    confirmed_rows = {
        int(value)
        for item in findings
        for value in re.findall(r"\d+", item["excel_rows"])
    }
    for match in matches:
        if (
            match["excel_row"] not in confirmed_rows
            and match["paid_difference"] is not None
            and abs(match["paid_difference"]) >= CENT
            and not match["excel_amount_in_paid_evidence"]
        ):
            findings.append(
                {
                    "kind": "diferencia_con_pago_documentado",
                    "excel_rows": str(match["excel_row"]),
                    "account": match["account"],
                    "current_amount": match["amount"],
                    "proposed_amount": match["paid_amount"],
                    "adjustment": match["paid_difference"],
                    "confidence": "media",
                    "affects_confirmed_total": False,
                    "evidence": f"{match['matched_path']}, página {match['paid_page']}",
                    "detail": "El pago extraído difiere; revisar si la relación usa importe bruto o neto.",
                }
            )
    return findings


def _display(value):
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, list):
        return json.dumps([_display(item) for item in value], ensure_ascii=False)
    return value


def csv_text(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows({key: _display(value) for key, value in row.items()} for row in rows)
    return "\ufeff" + output.getvalue()


def atomic_text(path, content):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="")
    os.replace(temporary, path)


def write_trace_tables(path, rows, links, source_hash):
    if not path or not Path(path).is_file():
        return False
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.executescript(
                """
                DROP TABLE IF EXISTS external_payment_links;
                DROP TABLE IF EXISTS external_payment_rows;
                CREATE TABLE external_payment_rows(
                  excel_row INTEGER PRIMARY KEY,account TEXT,origin TEXT,date_text TEXT,month TEXT,
                  amount_raw TEXT,amount TEXT,amount_status TEXT,concept TEXT,match_status TEXT,
                  match_confidence TEXT,match_score INTEGER,matched_path TEXT,matched_record_key TEXT,
                  matched_type TEXT,matched_page INTEGER,source_amount TEXT,amount_difference TEXT);
                CREATE TABLE external_payment_links(
                  excel_row INTEGER NOT NULL,record_key TEXT NOT NULL,relative_path TEXT NOT NULL,
                  score INTEGER NOT NULL,rank INTEGER NOT NULL,status TEXT NOT NULL,reasons TEXT NOT NULL,
                  PRIMARY KEY(excel_row,record_key));
                CREATE INDEX external_payments_match ON external_payment_rows(match_status,month);
                """
            )
            connection.executemany(
                "INSERT INTO external_payment_rows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        row["excel_row"], row["account"], row["origin"], row["date_text"], row["month"],
                        row["amount_raw"], _display(row["amount"]), row["amount_status"], row["concept"],
                        row["match_status"], row["match_confidence"], row["match_score"], row["matched_path"],
                        row["matched_record_key"], row["matched_type"], row["matched_page"] or None,
                        _display(row["source_amount"]), _display(row["amount_difference"]),
                    )
                    for row in rows
                ],
            )
            connection.executemany(
                "INSERT INTO external_payment_links VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        item["excel_row"], item["record_key"], item["relative_path"], item["score"],
                        item["rank"], item["status"], json.dumps(item["reasons"], ensure_ascii=False),
                    )
                    for item in links
                ],
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata VALUES(?,?)", ("external_payment_source_sha256", source_hash)
            )
        return True
    finally:
        connection.close()


def report_html(summary, findings, rows):
    finding_rows = "".join(
        "<tr>" + "".join(
            f"<td>{html.escape(str(_display(item[key]) or ''))}</td>"
            for key in (
                "kind", "excel_rows", "account", "adjustment", "confidence", "affects_confirmed_total",
                "evidence", "detail",
            )
        ) + "</tr>"
        for item in findings
    )
    pending = [row for row in rows if row["match_status"] != "vinculado"]
    pending_rows = "".join(
        f"<tr><td>{row['excel_row']}</td><td>{html.escape(row['account'])}</td>"
        f"<td>{html.escape(row['month'])}</td><td>{_display(row['amount'])}</td>"
        f"<td>{html.escape(row['match_status'])}</td><td>{html.escape(row['matched_path'])}</td></tr>"
        for row in pending
    )
    cards = [
        ("Total de Excel", summary["worksheet_total"]),
        ("Objetivo", summary["expected_total"]),
        ("Diferencia inicial", summary["initial_gap"]),
        ("Total con ajustes confirmados", summary["confirmed_adjusted_total"]),
        ("Diferencia pendiente", summary["remaining_gap"]),
    ]
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Conciliación de pagos</title>
<style>body{{font:15px system-ui;margin:0;background:#f5f7fa;color:#16202a}}header,section{{max-width:1200px;margin:18px auto;padding:22px;background:white;border-radius:12px}}h1{{margin-top:0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px}}.card{{padding:15px;background:#eef4ff;border-radius:9px;font-size:20px;font-weight:700}}.card small{{display:block;font-size:12px;font-weight:400}}table{{width:100%;border-collapse:collapse}}th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}th{{position:sticky;top:0;background:#eef4ff}}</style>
<header><h1>Conciliación de la relación de pagos</h1><p>Los ajustes se sustentan en la hoja de cálculo y en las páginas OCR vinculadas. La diferencia pendiente no se asigna sin evidencia documental.</p><div class="cards">{''.join(f'<div class="card">${value:,.2f}<small>{label}</small></div>' for label,value in cards)}</div></header>
<section><h2>Hallazgos confirmados o para revisión</h2><table><thead><tr><th>Tipo</th><th>Filas</th><th>Cuenta</th><th>Ajuste</th><th>Confianza</th><th>Aplicado</th><th>Evidencia</th><th>Detalle</th></tr></thead><tbody>{finding_rows}</tbody></table></section>
<section><h2>Vínculos pendientes o ambiguos</h2><p>{len(pending)} de {len(rows)} filas requieren revisión del vínculo.</p><table><thead><tr><th>Fila</th><th>Cuenta</th><th>Mes</th><th>Importe</th><th>Estado</th><th>Documento candidato</th></tr></thead><tbody>{pending_rows}</tbody></table></section></html>"""


def build(relation_path, extraction_database, output_dir, expected_total, trace_database=None, progress=print):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    relation_path = Path(relation_path).resolve()
    source_hash = hashlib.sha256(relation_path.read_bytes()).hexdigest()
    rows, stated_total = payment_rows(relation_path)
    progress(f"Leyendo {len(rows):,} filas de la relación de pagos…")
    uri = Path(extraction_database).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    records = [dict(item) for item in connection.execute("SELECT * FROM records ORDER BY id")]
    connection.close()
    progress(f"Cruzando contra {len(records):,} registros con evidencia…")
    matched_rows, links = match_rows(rows, records)
    findings = confirmed_findings(rows, matched_rows)
    worksheet_total = stated_total or sum((row["amount"] or Decimal() for row in rows), Decimal())
    expected_total = money(expected_total)
    confirmed_adjustment = sum(
        (item["adjustment"] for item in findings if item["affects_confirmed_total"]), Decimal()
    )
    adjusted_total = worksheet_total + confirmed_adjustment
    summary = {
        "source_file": str(relation_path),
        "source_sha256": source_hash,
        "rows": len(rows),
        "worksheet_total": worksheet_total,
        "expected_total": expected_total,
        "initial_gap": expected_total - worksheet_total,
        "confirmed_adjustment": confirmed_adjustment,
        "confirmed_adjusted_total": adjusted_total,
        "remaining_gap": expected_total - adjusted_total,
        "linked_rows": sum(row["match_status"] == "vinculado" for row in matched_rows),
        "ambiguous_rows": sum(row["match_status"] == "ambiguo" for row in matched_rows),
        "unmatched_rows": sum(row["match_status"] == "sin_coincidencia" for row in matched_rows),
        "confirmed_findings": sum(item["affects_confirmed_total"] for item in findings),
        "review_findings": sum(not item["affects_confirmed_total"] for item in findings),
    }
    row_fields = [
        "excel_row", "account", "origin", "date_text", "month", "amount_raw", "amount", "amount_status",
        "concept", "match_status", "match_confidence", "match_score", "match_reasons", "matched_path",
        "matched_record_key", "matched_type", "matched_page", "source_amount", "amount_difference",
        "paid_amounts", "paid_amount", "paid_page", "paid_difference", "excel_amount_in_paid_evidence",
    ]
    finding_fields = [
        "kind", "excel_rows", "account", "current_amount", "proposed_amount", "adjustment", "confidence",
        "affects_confirmed_total", "evidence", "detail",
    ]
    atomic_text(output_dir / "cruce_relacion_pagos.csv", csv_text(matched_rows, row_fields))
    atomic_text(output_dir / "hallazgos_relacion_pagos.csv", csv_text(findings, finding_fields))
    serializable_summary = {key: _display(value) for key, value in summary.items()}
    atomic_text(
        output_dir / "resumen_relacion_pagos.json",
        json.dumps(serializable_summary, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_text(output_dir / "conciliacion_relacion_pagos.html", report_html(summary, findings, matched_rows))
    summary["trace_database_updated"] = write_trace_tables(trace_database, matched_rows, links, source_hash)
    progress(
        f"Cruce terminado: {summary['linked_rows']} vinculadas, {summary['ambiguous_rows']} ambiguas y "
        f"{summary['unmatched_rows']} sin vínculo."
    )
    return {key: _display(value) for key, value in summary.items()}
