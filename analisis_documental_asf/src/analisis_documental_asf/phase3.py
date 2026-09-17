"""Phase 3: evidence-backed reconciliation and payment traceability."""

import csv
import hashlib
import html
import io
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from pathlib import Path

TRACE_SCHEMA_VERSION = 1
PAYMENT_TYPES = {"poliza_orden_pago", "cheque", "lote_transferencia", "movimiento_bancario"}
TYPE_LABELS = {
    "poliza_orden_pago": "Póliza / orden",
    "cheque": "Cheque",
    "lote_transferencia": "Transferencia",
    "movimiento_bancario": "Movimiento bancario",
    "recibo_nomina": "Recibo de nómina",
}
MONTHS = {
    "ENE": 1,
    "ENERO": 1,
    "FEB": 2,
    "FEBRERO": 2,
    "MAR": 3,
    "MARZO": 3,
    "ABR": 4,
    "ABRIL": 4,
    "MAY": 5,
    "MAYO": 5,
    "JUN": 6,
    "JUNIO": 6,
    "JUL": 7,
    "JULIO": 7,
    "AGO": 8,
    "AGOSTO": 8,
    "SEP": 9,
    "SEPTIEMBRE": 9,
    "OCT": 10,
    "OCTUBRE": 10,
    "NOV": 11,
    "NOVIEMBRE": 11,
    "DIC": 12,
    "DICIEMBRE": 12,
}


def fold(value):
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(character for character in value if not unicodedata.combining(character)).upper()


def normalize_date(value):
    value = fold(value).strip()
    iso = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", value)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))).isoformat()
        except ValueError:
            return ""
    written = re.search(r"(\d{1,2})\s+(?:DE\s+)?([A-Z]+)\s+(?:DE\s+)?(20\d{2})", value)
    if written and written.group(2) in MONTHS:
        try:
            return date(int(written.group(3)), MONTHS[written.group(2)], int(written.group(1))).isoformat()
        except ValueError:
            return ""
    numeric = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](20\d{2}|\d{2})", value)
    if numeric:
        year = int(numeric.group(3))
        if year < 100:
            year += 2000
        try:
            return date(year, int(numeric.group(2)), int(numeric.group(1))).isoformat()
        except ValueError:
            return ""
    return ""


def date_distance(left, right):
    left = normalize_date(left)
    right = normalize_date(right)
    if not left or not right:
        return None
    return abs((date.fromisoformat(left) - date.fromisoformat(right)).days)


def normalize_name(value):
    value = re.sub(r"[^A-Z ]", " ", fold(value))
    stopwords = {
        "MUNICIPIO",
        "DE",
        "DEL",
        "LA",
        "EL",
        "EMILIANO",
        "ZAPATA",
        "VER",
        "VERACRUZ",
        "PERSONAL",
    }
    return " ".join(word for word in value.split() if word not in stopwords)


def name_similarity(left, right):
    left = normalize_name(left)
    right = normalize_name(right)
    if not left or not right:
        return 0.0
    left_words = set(left.split())
    right_words = set(right.split())
    overlap = len(left_words & right_words) / len(left_words | right_words)
    return max(overlap, SequenceMatcher(None, left, right).ratio())


def same_amount(left, right):
    return bool(left.get("amount") and left["amount"] == right.get("amount"))


def policy_cheque_score(policy, cheque):
    if policy["document_id"] != cheque["document_id"]:
        return None
    score = 0
    reasons = []
    distance = abs(policy["page_number"] - cheque["page_number"])
    numbers = set(re.findall(r"\bCH\s*[-: ]\s*(\d{1,8})", fold(policy.get("evidence", ""))))
    if cheque.get("folio") and cheque["folio"] in numbers and distance <= 15:
        score += 70
        reasons.append("folio_cheque_en_poliza")
    if same_amount(policy, cheque):
        score += 25
        reasons.append("importe_exacto")
    similarity = name_similarity(policy.get("beneficiary"), cheque.get("beneficiary"))
    if similarity >= 0.82:
        score += 25
        reasons.append("beneficiario_coincidente")
    elif similarity >= 0.62:
        score += 12
        reasons.append("beneficiario_similar")
    if distance <= 3:
        score += 15
        reasons.append("paginas_cercanas")
    elif distance <= 8:
        score += 10
        reasons.append("paginas_proximas")
    elif distance <= 15:
        score += 5
        reasons.append("mismo_bloque_documental")
    days = date_distance(policy.get("operation_date"), cheque.get("operation_date"))
    if days == 0:
        score += 10
        reasons.append("fecha_exacta")
    elif days is not None and days <= 3:
        score += 5
        reasons.append("fecha_cercana")
    explicit_supported = "folio_cheque_en_poliza" in reasons and (
        "importe_exacto" in reasons or similarity >= 0.62 or distance <= 8
    )
    eligible = explicit_supported or (
        "importe_exacto" in reasons
        and ("paginas_cercanas" in reasons or "paginas_proximas" in reasons or similarity >= 0.62)
    )
    return (score, reasons) if eligible else None


def cheque_transfer_score(cheque, transfer):
    if cheque["document_id"] != transfer["document_id"]:
        return None
    score = 0
    reasons = []
    if cheque.get("folio") and cheque["folio"] == transfer.get("folio"):
        score += 55
        reasons.append("folio_exacto")
    if same_amount(cheque, transfer):
        score += 35
        reasons.append("importe_exacto")
    days = date_distance(cheque.get("operation_date"), transfer.get("operation_date"))
    if days == 0:
        score += 20
        reasons.append("fecha_exacta")
    elif days is not None and days <= 3:
        score += 10
        reasons.append("fecha_cercana")
    distance = abs(cheque["page_number"] - transfer["page_number"])
    if distance <= 3:
        score += 15
        reasons.append("paginas_cercanas")
    elif distance <= 8:
        score += 8
        reasons.append("paginas_proximas")
    return (score, reasons) if reasons and ("folio_exacto" in reasons or "importe_exacto" in reasons) else None


def policy_transfer_score(policy, transfer):
    if policy["document_id"] != transfer["document_id"] or not same_amount(policy, transfer):
        return None
    score = 45
    reasons = ["importe_exacto"]
    days = date_distance(policy.get("operation_date"), transfer.get("operation_date"))
    if days == 0:
        score += 25
        reasons.append("fecha_exacta")
    elif days is not None and days <= 3:
        score += 15
        reasons.append("fecha_cercana")
    if abs(policy["page_number"] - transfer["page_number"]) <= 8:
        score += 10
        reasons.append("paginas_proximas")
    return score, reasons


class UnionFind:
    def __init__(self, keys):
        self.parent = {key: key for key in keys}

    def find(self, key):
        while self.parent[key] != key:
            self.parent[key] = self.parent[self.parent[key]]
            key = self.parent[key]
        return key

    def union(self, left, right):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def link_row(left, right, relation, score, reasons, status):
    raw = f"{left['record_key']}|{right['record_key']}|{relation}"
    return {
        "link_id": hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24],
        "source_key": left["record_key"],
        "target_key": right["record_key"],
        "relation": relation,
        "score": min(score, 100),
        "confidence": "alta" if status == "aceptado" else "media",
        "status": status,
        "reasons": reasons,
    }


def choose_internal_links(candidates, direct_reason, relation, union):
    links = []
    direct = [item for item in candidates if direct_reason in item[2]]
    accepted_pairs = set()
    direct_by_left = defaultdict(list)
    for item in direct:
        direct_by_left[item[0]["record_key"]].append(item)
    for options in direct_by_left.values():
        ordered = sorted(options, key=lambda item: item[3], reverse=True)
        unambiguous = len(ordered) == 1 or ordered[0][3] >= ordered[1][3] + 10
        for index, (left, right, reasons, score) in enumerate(ordered):
            status = "aceptado" if unambiguous and index == 0 else "sugerido"
            if status == "aceptado":
                union.union(left["record_key"], right["record_key"])
                accepted_pairs.add((left["record_key"], right["record_key"]))
            if score >= 55:
                links.append(link_row(left, right, relation, score, reasons, status))

    inferred = [item for item in candidates if direct_reason not in item[2]]
    best_left = defaultdict(list)
    best_right = defaultdict(list)
    for item in inferred:
        best_left[item[0]["record_key"]].append(item)
        best_right[item[1]["record_key"]].append(item)

    def unique_best(items):
        ordered = sorted(items, key=lambda item: item[3], reverse=True)
        return ordered[0] if len(ordered) == 1 or ordered[0][3] >= ordered[1][3] + 10 else None

    for item in sorted(inferred, key=lambda value: value[3], reverse=True):
        left, right, reasons, score = item
        pair = (left["record_key"], right["record_key"])
        left_best = unique_best(best_left[left["record_key"]])
        right_best = unique_best(best_right[right["record_key"]])
        accepted = (
            score >= 75
            and left_best is item
            and right_best is item
            and pair not in accepted_pairs
        )
        status = "aceptado" if accepted else "sugerido"
        if accepted:
            union.union(*pair)
            accepted_pairs.add(pair)
        if score >= 55:
            links.append(link_row(left, right, relation, score, reasons, status))
    return links


def bank_candidate_score(bank, candidate):
    if not same_amount(bank, candidate):
        return None
    days = date_distance(bank.get("operation_date"), candidate.get("operation_date"))
    if days is None or days > 7:
        return None
    score = 35
    reasons = ["importe_exacto"]
    if days == 0:
        score += 30
        reasons.append("fecha_exacta")
    elif days <= 3:
        score += 24
        reasons.append("fecha_cercana")
    else:
        score += 15
        reasons.append("fecha_en_7_dias")
    similarity = name_similarity(bank.get("beneficiary"), candidate.get("beneficiary"))
    if similarity >= 0.86:
        score += 35
        reasons.append("beneficiario_coincidente")
    elif similarity >= 0.70:
        score += 22
        reasons.append("beneficiario_similar")
    if candidate["record_type"] == "lote_transferencia":
        score += 10
        reasons.append("lote_transferencia")
    bank_reference_tokens = set(re.findall(r"\b[A-Z0-9]{4,}\b", fold(bank.get("reference", ""))))
    if candidate.get("folio") and candidate["folio"] in bank_reference_tokens:
        score += 35
        reasons.append("folio_en_referencia_bancaria")
    return min(score, 100), reasons


def canonical_value(component, field, priority):
    by_type = defaultdict(list)
    for item in component:
        value = normalize_date(item[field]) if field == "operation_date" else item.get(field, "")
        if value:
            by_type[item["record_type"]].append(value)
    for record_type in priority:
        if by_type[record_type]:
            return Counter(by_type[record_type]).most_common(1)[0][0]
    return ""


def build_case(component, accepted_links, suggested_count):
    keys = {item["record_key"] for item in component}
    links = [
        item
        for item in accepted_links
        if item["source_key"] in keys and item["target_key"] in keys and item["status"] == "aceptado"
    ]
    record_types = {item["record_type"] for item in component}
    stages = [record_type for record_type in TYPE_LABELS if record_type in record_types]
    case_type = "nomina" if "recibo_nomina" in record_types else "pago"
    has_bank = "movimiento_bancario" in record_types
    if has_bank and len(record_types) > 1:
        status = "conciliado_banco"
    elif len(record_types) > 1:
        status = "trazabilidad_documental"
    elif case_type == "nomina":
        status = "detalle_nomina"
    else:
        status = "sin_conciliar"
    missing = []
    if case_type == "pago":
        if "poliza_orden_pago" not in record_types:
            missing.append("poliza_orden_pago")
        if not ({"cheque", "lote_transferencia"} & record_types):
            missing.append("medio_pago")
        if not has_bank:
            missing.append("movimiento_bancario")
    elif not has_bank:
        missing.append("vinculo_pago_bancario")
    amount = canonical_value(
        component,
        "amount",
        ("movimiento_bancario", "lote_transferencia", "cheque", "recibo_nomina", "poliza_orden_pago"),
    )
    operation_date = canonical_value(
        component,
        "operation_date",
        ("movimiento_bancario", "lote_transferencia", "cheque", "recibo_nomina", "poliza_orden_pago"),
    )
    beneficiary = canonical_value(
        component,
        "beneficiary",
        ("recibo_nomina", "cheque", "movimiento_bancario", "poliza_orden_pago"),
    )
    amounts_by_stage = {
        record_type: sorted({item["amount"] for item in component if item["record_type"] == record_type and item["amount"]})
        for record_type in stages
    }
    distinct_amounts = {value for values in amounts_by_stage.values() for value in values}
    digest = "|".join(sorted(keys))
    case_id = "TR-" + hashlib.sha256(digest.encode("utf-8")).hexdigest()[:16].upper()
    return {
        "case_id": case_id,
        "case_type": case_type,
        "status": status,
        "confidence": "alta" if links else "media",
        "operation_date": operation_date,
        "period": component[0]["relative_path"].replace("\\", "/").split("/", 1)[0],
        "amount": amount,
        "beneficiary": beneficiary,
        "stages": stages,
        "missing_stages": missing,
        "record_count": len(component),
        "document_count": len({item["document_id"] for item in component}),
        "accepted_links": len(links),
        "suggested_links": suggested_count,
        "amount_consistent": len(distinct_amounts) <= 1,
        "amounts_by_stage": amounts_by_stage,
        "records": sorted(component, key=lambda item: (item["relative_path"], item["page_number"], item["record_type"])),
    }


def reconcile(records):
    records = [dict(item) for item in records]
    by_type = defaultdict(list)
    by_document = defaultdict(lambda: defaultdict(list))
    for item in records:
        by_type[item["record_type"]].append(item)
        by_document[item["document_id"]][item["record_type"]].append(item)
    union = UnionFind(item["record_key"] for item in records)
    links = []

    relation_specs = (
        ("poliza_cheque", "poliza_orden_pago", "cheque", policy_cheque_score, "folio_cheque_en_poliza"),
        ("cheque_transferencia", "cheque", "lote_transferencia", cheque_transfer_score, "folio_exacto"),
        (
            "poliza_transferencia",
            "poliza_orden_pago",
            "lote_transferencia",
            policy_transfer_score,
            "__sin_clave_directa__",
        ),
    )
    for relation, left_type, right_type, scorer, direct_reason in relation_specs:
        candidates = []
        for grouped in by_document.values():
            for left in grouped[left_type]:
                for right in grouped[right_type]:
                    result = scorer(left, right)
                    if result:
                        score, reasons = result
                        candidates.append((left, right, reasons, score))
        links.extend(choose_internal_links(candidates, direct_reason, relation, union))

    components = defaultdict(list)
    for item in records:
        if item["record_type"] != "movimiento_bancario":
            components[union.find(item["record_key"])].append(item)
    banks = by_type["movimiento_bancario"]
    bank_decisions = []
    for bank in banks:
        choices = []
        for root, component in components.items():
            best = None
            for candidate in component:
                if candidate["record_type"] not in {"cheque", "lote_transferencia", "recibo_nomina"}:
                    continue
                result = bank_candidate_score(bank, candidate)
                if result and (best is None or result[0] > best[0]):
                    best = (result[0], result[1], candidate)
            if best:
                choices.append((best[0], best[1], best[2], root))
        choices.sort(key=lambda item: item[0], reverse=True)
        if not choices:
            continue
        top = choices[0]
        margin = top[0] - choices[1][0] if len(choices) > 1 else 100
        bank_decisions.append((bank, top, margin))

    used_components = set()
    for bank, top, margin in sorted(bank_decisions, key=lambda item: item[1][0], reverse=True):
        score, reasons, candidate, root = top
        accepted = score >= 75 and margin >= 10 and root not in used_components
        status = "aceptado" if accepted else "sugerido"
        if accepted:
            union.union(bank["record_key"], candidate["record_key"])
            used_components.add(root)
        if score >= 55:
            links.append(link_row(candidate, bank, "registro_movimiento_bancario", score, reasons, status))

    final_components = defaultdict(list)
    for item in records:
        final_components[union.find(item["record_key"])].append(item)
    suggested_by_key = Counter()
    for link in links:
        if link["status"] == "sugerido":
            suggested_by_key[link["source_key"]] += 1
            suggested_by_key[link["target_key"]] += 1
    cases = []
    for component in final_components.values():
        suggestion_count = sum(suggested_by_key[item["record_key"]] for item in component)
        cases.append(build_case(component, links, suggestion_count))
    cases.sort(key=lambda item: (item["period"], item["operation_date"], item["case_id"]))
    return cases, links


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".phase3-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def csv_text(items, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(items)
    return "\ufeff" + output.getvalue()


def local_link(path, page):
    try:
        return f"{Path(path).resolve().as_uri()}#page={page}" if path else ""
    except ValueError:
        return ""


def case_row(item):
    row = {
        key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
        for key, value in item.items()
        if key != "records"
    }
    row["documents"] = json.dumps(
        sorted({record["relative_path"] for record in item["records"]}), ensure_ascii=False
    )
    row["record_keys"] = json.dumps([record["record_key"] for record in item["records"]])
    return row


def build_html(summary, cases):
    cards = []
    for item in cases:
        records = "".join(
            f'<li><a href="{html.escape(local_link(record["pdf_path"], record["page_number"]))}">'
            f'{html.escape(TYPE_LABELS.get(record["record_type"], record["record_type"]))}: '
            f'{html.escape(record["relative_path"])} · página {record["page_number"]}</a>'
            f' · {html.escape(record.get("amount") or "sin importe")}'
            f'{" · folio " + html.escape(record["folio"]) if record.get("folio") else ""}</li>'
            for record in item["records"]
        )
        searchable = " ".join(
            [item["beneficiary"], item["amount"], item["case_id"]]
            + [
                " ".join(
                    (
                        record["relative_path"],
                        record.get("folio", ""),
                        record.get("reference", ""),
                        record.get("beneficiary", ""),
                    )
                )
                for record in item["records"]
            ]
        )
        amount_note = "" if item["amount_consistent"] else "<p><strong>Importes distintos entre etapas</strong></p>"
        cards.append(
            f'<article data-status="{item["status"]}" data-kind="{item["case_type"]}" '
            f'data-confidence="{item["confidence"]}"><h3>{item["case_id"]}</h3>'
            f'<p><strong>{html.escape(item["status"])}</strong> · {html.escape(item["period"])} · '
            f'{html.escape(item["amount"] or "sin importe")}</p><p>{html.escape(item["beneficiary"] or "—")}</p>'
            f'<p>Etapas: {html.escape(", ".join(TYPE_LABELS.get(value, value) for value in item["stages"]))}</p>'
            f'<p>Faltantes: {html.escape(", ".join(item["missing_stages"]) or "ninguno")}</p>'
            f'{amount_note}'
            f'<details><summary>{item["record_count"]} registros y {item["accepted_links"]} vínculos</summary>'
            f'<ul>{records}</ul><pre>{html.escape(json.dumps(item["amounts_by_stage"], ensure_ascii=False, indent=2))}</pre>'
            f'</details><span class="searchable">{html.escape(searchable)}</span></article>'
        )
    status_options = "".join(
        f'<option value="{html.escape(status)}">{html.escape(status)}</option>'
        for status in sorted({item["status"] for item in cases})
    )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Trazabilidad documental ASF — Fase 3</title><style>
body{{font:15px system-ui;background:#edf2f5;color:#183241;max-width:1500px;margin:auto;padding:24px}}header,section,article{{background:white;border-radius:12px;padding:20px;margin:14px 0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}.card{{background:#174d6f;color:white;padding:18px;border-radius:10px;font-size:21px}}.controls{{display:flex;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:2}}input,select{{padding:11px;min-width:220px;font:inherit}}article h3{{margin-bottom:4px}}.searchable{{display:none}}[hidden]{{display:none}}pre{{white-space:pre-wrap}}a{{color:#075985}}</style>
<header><h1>Trazabilidad documental — Fase 3</h1><p>Relaciona representaciones de una operación sin sumar varias veces la póliza, el cheque, la transferencia y el movimiento bancario.</p><div class="cards"><div class="card">{summary['cases']:,}<small><br>casos</small></div><div class="card">{summary['linked_cases']:,}<small><br>con trazabilidad</small></div><div class="card">{summary['bank_reconciled']:,}<small><br>conciliados con banco</small></div><div class="card">{summary['accepted_links']:,}<small><br>vínculos aceptados</small></div></div></header>
<section><p>Los importes consolidados son candidatos de control. Nómina y pagos se muestran por separado porque un lote puede contener múltiples recibos.</p></section>
<section class="controls"><input id="q" type="search" placeholder="Buscar folio, nombre, importe o archivo…"><select id="status"><option value="">Todos los estados</option>{status_options}</select><select id="kind"><option value="">Todos los grupos</option><option value="pago">Pagos</option><option value="nomina">Nómina</option></select><strong id="count"></strong></section>
<main>{''.join(cards)}</main><script>const all=[...document.querySelectorAll('article')],q=document.querySelector('#q'),status=document.querySelector('#status'),kind=document.querySelector('#kind'),count=document.querySelector('#count');function filter(){{let query=q.value.toLocaleLowerCase(),n=0;all.forEach(x=>{{let show=(!query||x.textContent.toLocaleLowerCase().includes(query))&&(!status.value||x.dataset.status===status.value)&&(!kind.value||x.dataset.kind===kind.value);x.hidden=!show;if(show)n++}});count.textContent=n+' visibles'}}[q,status,kind].forEach(x=>x.addEventListener('input',filter));filter();</script></html>"""


def write_database(path, records, cases, links, source_database):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.building")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE source_records(record_key TEXT PRIMARY KEY,record_type TEXT NOT NULL,document_id TEXT NOT NULL,
              relative_path TEXT NOT NULL,pdf_path TEXT,page_number INTEGER NOT NULL,confidence TEXT,operation_date TEXT,
              normalized_date TEXT,amount TEXT,beneficiary TEXT,folio TEXT,reference TEXT,concept TEXT,evidence TEXT,extra TEXT);
            CREATE TABLE cases(case_id TEXT PRIMARY KEY,case_type TEXT NOT NULL,status TEXT NOT NULL,confidence TEXT NOT NULL,
              operation_date TEXT,period TEXT,amount TEXT,beneficiary TEXT,stages TEXT,missing_stages TEXT,record_count INTEGER,
              document_count INTEGER,accepted_links INTEGER,suggested_links INTEGER,amount_consistent INTEGER,amounts_by_stage TEXT);
            CREATE TABLE case_records(case_id TEXT NOT NULL,record_key TEXT NOT NULL,PRIMARY KEY(case_id,record_key),
              FOREIGN KEY(case_id) REFERENCES cases(case_id),FOREIGN KEY(record_key) REFERENCES source_records(record_key));
            CREATE TABLE links(link_id TEXT PRIMARY KEY,source_key TEXT NOT NULL,target_key TEXT NOT NULL,relation TEXT NOT NULL,
              score INTEGER NOT NULL,confidence TEXT NOT NULL,status TEXT NOT NULL,reasons TEXT NOT NULL,
              FOREIGN KEY(source_key) REFERENCES source_records(record_key),FOREIGN KEY(target_key) REFERENCES source_records(record_key));
            CREATE INDEX cases_status ON cases(status,case_type);
            CREATE INDEX cases_period ON cases(period);
            CREATE INDEX links_status ON links(status,relation);
            """
        )
        with connection:
            connection.executemany(
                "INSERT INTO source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        item["record_key"],
                        item["record_type"],
                        item["document_id"],
                        item["relative_path"],
                        item["pdf_path"],
                        item["page_number"],
                        item["confidence"],
                        item["operation_date"],
                        normalize_date(item["operation_date"]),
                        item["amount"],
                        item["beneficiary"],
                        item["folio"],
                        item["reference"],
                        item["concept"],
                        item["evidence"],
                        item["extra"],
                    )
                    for item in records
                ],
            )
            case_fields = [
                "case_id",
                "case_type",
                "status",
                "confidence",
                "operation_date",
                "period",
                "amount",
                "beneficiary",
                "stages",
                "missing_stages",
                "record_count",
                "document_count",
                "accepted_links",
                "suggested_links",
                "amount_consistent",
                "amounts_by_stage",
            ]
            connection.executemany(
                f"INSERT INTO cases VALUES({','.join('?' for _ in case_fields)})",
                [
                    tuple(
                        json.dumps(item[field], ensure_ascii=False)
                        if isinstance(item[field], (list, dict))
                        else item[field]
                        for field in case_fields
                    )
                    for item in cases
                ],
            )
            connection.executemany(
                "INSERT INTO case_records VALUES(?,?)",
                [
                    (case["case_id"], record["record_key"])
                    for case in cases
                    for record in case["records"]
                ],
            )
            connection.executemany(
                "INSERT INTO links VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        item["link_id"],
                        item["source_key"],
                        item["target_key"],
                        item["relation"],
                        item["score"],
                        item["confidence"],
                        item["status"],
                        json.dumps(item["reasons"], ensure_ascii=False),
                    )
                    for item in links
                ],
            )
            connection.executemany(
                "INSERT INTO metadata VALUES(?,?)",
                [
                    ("schema_version", str(TRACE_SCHEMA_VERSION)),
                    ("source_database", str(Path(source_database).resolve())),
                    ("source_records", str(len(records))),
                ],
            )
        connection.close()
        os.replace(temporary, path)
    except Exception:
        connection.close()
        temporary.unlink(missing_ok=True)
        raise


def build(source_database, output_dir, progress=print):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    uri = Path(source_database).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    records = [dict(item) for item in connection.execute("SELECT * FROM records ORDER BY id")]
    connection.close()
    progress(f"Conciliando {len(records):,} registros estructurados…")
    cases, links = reconcile(records)
    accepted = [item for item in links if item["status"] == "aceptado"]
    suggested = [item for item in links if item["status"] == "sugerido"]
    summary = {
        "source_records": len(records),
        "cases": len(cases),
        "payment_cases": sum(item["case_type"] == "pago" for item in cases),
        "payroll_cases": sum(item["case_type"] == "nomina" for item in cases),
        "linked_cases": sum(item["record_count"] > 1 for item in cases),
        "bank_reconciled": sum(item["status"] == "conciliado_banco" for item in cases),
        "document_traces": sum(item["status"] == "trazabilidad_documental" for item in cases),
        "unreconciled_payments": sum(
            item["case_type"] == "pago" and item["status"] == "sin_conciliar" for item in cases
        ),
        "accepted_links": len(accepted),
        "suggested_links": len(suggested),
    }
    write_database(output_dir / "trazabilidad.sqlite3", records, cases, links, source_database)
    case_rows = [case_row(item) for item in cases]
    case_fields = list(case_rows[0]) if case_rows else ["case_id"]
    atomic_text(output_dir / "trazabilidad_operaciones.csv", csv_text(case_rows, case_fields))
    records_by_key = {item["record_key"]: item for item in records}
    link_rows = [
        {
            **item,
            "reasons": json.dumps(item["reasons"], ensure_ascii=False),
            "source_type": records_by_key[item["source_key"]]["record_type"],
            "source_path": records_by_key[item["source_key"]]["relative_path"],
            "source_page": records_by_key[item["source_key"]]["page_number"],
            "source_amount": records_by_key[item["source_key"]]["amount"],
            "target_type": records_by_key[item["target_key"]]["record_type"],
            "target_path": records_by_key[item["target_key"]]["relative_path"],
            "target_page": records_by_key[item["target_key"]]["page_number"],
            "target_amount": records_by_key[item["target_key"]]["amount"],
        }
        for item in links
    ]
    link_fields = list(link_rows[0]) if link_rows else ["link_id"]
    atomic_text(output_dir / "vinculos_trazabilidad.csv", csv_text(link_rows, link_fields))
    pending = [
        case_row(item)
        for item in cases
        if item["case_type"] == "pago" and item["status"] == "sin_conciliar"
    ]
    atomic_text(output_dir / "pendientes_trazabilidad.csv", csv_text(pending, case_fields))
    suggested_rows = [item for item in link_rows if item["status"] == "sugerido"]
    atomic_text(output_dir / "vinculos_sugeridos.csv", csv_text(suggested_rows, link_fields))

    linked_keys = {
        record["record_key"]
        for case in cases
        if case["record_count"] > 1
        for record in case["records"]
    }
    coverage_rows = []
    for record_type, total in Counter(item["record_type"] for item in records).most_common():
        linked = sum(item["record_type"] == record_type and item["record_key"] in linked_keys for item in records)
        coverage_rows.append(
            {
                "tipo_registro": record_type,
                "total": total,
                "vinculados": linked,
                "sin_vincular": total - linked,
                "porcentaje_vinculado": format(Decimal(linked * 100) / Decimal(total), ".2f"),
            }
        )
    atomic_text(
        output_dir / "cobertura_trazabilidad.csv",
        csv_text(coverage_rows, list(coverage_rows[0]) if coverage_rows else ["tipo_registro"]),
    )

    monthly = defaultdict(lambda: {"casos": 0, "trazados": 0, "conciliados_banco": 0, "importe_candidato": Decimal("0")})
    for item in cases:
        if item["case_type"] != "pago":
            continue
        monthly[item["period"]]["casos"] += 1
        monthly[item["period"]]["trazados"] += item["record_count"] > 1
        monthly[item["period"]]["conciliados_banco"] += item["status"] == "conciliado_banco"
        if item["amount"]:
            monthly[item["period"]]["importe_candidato"] += Decimal(item["amount"])
    monthly_rows = [
        {
            "periodo": period,
            **{
                key: format(value, ".2f") if key == "importe_candidato" else value
                for key, value in values.items()
            },
        }
        for period, values in sorted(
            monthly.items(),
            key=lambda item: (
                int(re.match(r"\d+", item[0]).group()) if re.match(r"\d+", item[0]) else 99,
                item[0],
            ),
        )
    ]
    monthly_fields = list(monthly_rows[0]) if monthly_rows else ["periodo"]
    atomic_text(output_dir / "resumen_trazabilidad_por_mes.csv", csv_text(monthly_rows, monthly_fields))
    atomic_text(output_dir / "resumen_fase3.json", json.dumps(summary, ensure_ascii=False, indent=2))
    atomic_text(output_dir / "trazabilidad_documental.html", build_html(summary, cases))
    progress(f"Fase 3 terminada: {len(cases):,} casos y {len(accepted):,} vínculos aceptados.")
    return summary
