"""Phase 2: structured, evidence-backed extraction from OCR page text."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation

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

RFC = re.compile(r"(?<![A-Z0-9Ñ&])([A-ZÑ&]{3,4}\d{6}\s*[A-Z0-9]{3})(?![A-Z0-9])", re.I)
CURP = re.compile(r"(?<![A-Z0-9])([A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]{2})(?![A-Z0-9])", re.I)
CLABE = re.compile(r"(?<!\d)(\d{18})(?!\d)")
UUID = re.compile(
    r"(?<![A-F0-9])([A-F0-9]{8}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{4}-[A-F0-9]{12})(?![A-F0-9])",
    re.I,
)
MONEY = re.compile(
    r"(?<![\d.])(\$[ \t]{0,5}\d[\d,]{0,20}\.\d{2}|\d{1,3}(?:,\d{3})+\.\d{2}|\d{1,12}\.\d{2})(?![\d.])"
)
DATE_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](20\d{2}|\d{2})(?!\d)")
LABELED_ACCOUNT = re.compile(
    r"(?:CUENTA(?:\s+DE\s+(?:CARGO|ABONO))?|CTA\.?|NO\.?\s*DE\s*CUENTA)\s*(?:NO\.?)?\s*[:#-]?\s*(\d[\d ]{7,22})",
    re.I,
)
LABELED_FOLIO = re.compile(
    r"(?:FOLIO(?:\s+(?:FISCAL|UNICO|DEL\s+LOTE))?|REFERENCIA|REF\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_-]{3,45})",
    re.I,
)


def fold(value):
    value = unicodedata.normalize("NFKD", value)
    return "".join(character for character in value if not unicodedata.combining(character)).upper()


def clean_space(value):
    return re.sub(r"\s+", " ", value).strip(" |:;,-")


def snippet(text, start, end, radius=100):
    return clean_space(text[max(0, start - radius) : min(len(text), end + radius)])


def money_value(raw):
    negative = "(" in raw and ")" in raw
    cleaned = raw.replace("$", "").replace(",", "").replace(" ", "").strip("()")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def normalized_date(day, month, year):
    year = int(year)
    if year < 100:
        year += 2000
    try:
        return date(year, int(month), int(day)).isoformat()
    except ValueError:
        return None


def valid_embedded_date(value):
    match = re.search(r"\d{6}", value)
    if not match:
        return False
    year, month, day = int(match.group()[:2]), int(match.group()[2:4]), int(match.group()[4:])
    try:
        date(2000 + year, month, day)
    except ValueError:
        return False
    return True


def entity(entity_type, value, normalized, label, confidence, text, match):
    return {
        "entity_type": entity_type,
        "value": clean_space(value),
        "normalized_value": str(normalized),
        "label": label,
        "confidence": confidence,
        "snippet": snippet(text, match.start(), match.end()),
    }


def extract_entities(text):
    """Extract exact candidates. Amounts remain candidates, not accounting conclusions."""
    upper = text.upper()
    folded = fold(text)
    found = []
    for pattern, kind, label in (
        (RFC, "rfc", "RFC"),
        (CURP, "curp", "CURP"),
        (CLABE, "clabe", "CLABE o número de 18 dígitos"),
        (UUID, "uuid", "UUID fiscal"),
    ):
        for match in pattern.finditer(upper):
            value = re.sub(r"\s+", "", match.group(1)).upper()
            confidence = "alta"
            entity_label = label
            if kind in {"rfc", "curp"} and not valid_embedded_date(value):
                confidence = "media"
                entity_label = f"{label} con posible error OCR"
            if kind == "curp" and not value[-1].isdigit():
                confidence = "media"
                entity_label = "CURP con posible error OCR"
            found.append(entity(kind, match.group(1), value, entity_label, confidence, text, match))

    for match in LABELED_ACCOUNT.finditer(folded):
        value = re.sub(r"\D", "", match.group(1))
        if 8 <= len(value) <= 20:
            found.append(entity("cuenta", match.group(1), value, "cuenta etiquetada", "alta", text, match))

    for match in LABELED_FOLIO.finditer(folded):
        value = match.group(1).upper().strip("-_/")
        if len(value) >= 4:
            found.append(entity("folio_referencia", match.group(1), value, "folio o referencia", "alta", text, match))

    for match in DATE_NUMERIC.finditer(folded):
        value = normalized_date(*match.groups())
        if value:
            found.append(entity("fecha", match.group(0), value, "fecha numérica", "alta", text, match))

    for match in MONEY.finditer(text):
        value = money_value(match.group(1))
        if value is None:
            continue
        before = fold(text[max(0, match.start() - 70) : match.start()])
        known = next(
            (
                label
                for signal, label in (
                    ("NETO DEL RECIBO", "neto del recibo"),
                    ("IMPORTE TOTAL", "importe total"),
                    ("CIFRA DE CONTROL", "cifra de control"),
                    ("SALDO FINAL", "saldo final"),
                    ("TOTAL", "total"),
                    ("CANTIDAD", "cantidad"),
                    ("DEBE", "debe o haber"),
                    ("HABER", "debe o haber"),
                )
                if signal in before
            ),
            "importe mencionado",
        )
        confidence = "alta" if known != "importe mencionado" or "$" in match.group(1) else "media"
        found.append(entity("importe", match.group(1), format(value, ".2f"), known, confidence, text, match))

    unique = {}
    for item in found:
        key = (item["entity_type"], item["normalized_value"], item["label"])
        unique.setdefault(key, item)
    return list(unique.values())


def first_group(pattern, text, flags=re.I | re.M):
    match = re.search(pattern, text, flags)
    return clean_space(match.group(1)) if match else ""


def first_money(pattern, text):
    raw = first_group(pattern, text, re.I | re.M | re.S)
    value = money_value(raw) if raw else None
    return format(value, ".2f") if value is not None else ""


def clean_person_name(value):
    value = clean_space(value).strip("._-|:;")
    value = re.sub(r"RFC$", "", value, flags=re.I).strip()
    folded = fold(value)
    if re.search(r"\d", value) or any(
        word in folded for word in (" RFC", "ISR", "ASIMILADOS", "PERIODO", "FECHA", "EJERCICIO")
    ):
        return ""
    words = re.findall(r"[A-ZÁÉÍÓÚÜÑ]{2,}", value.upper())
    return value if 2 <= len(words) <= 8 else ""


def parse_date_text(value):
    value = fold(value)
    match = re.search(r"(\d{1,2})[/-]([A-Z]{3}|\d{1,2})[/-](20\d{2}|\d{2})", value)
    if not match:
        return ""
    month = MONTHS.get(match.group(2), match.group(2))
    return normalized_date(match.group(1), month, match.group(3)) or ""


def record_key(record_type, document_id, discriminator):
    raw = f"{record_type}|{document_id}|{discriminator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def base_record(record_type, document, page_number, confidence, evidence):
    return {
        "record_type": record_type,
        "document_id": document["id"],
        "relative_path": document["relative_path"],
        "pdf_path": document.get("pdf_path", ""),
        "page_number": page_number,
        "confidence": confidence,
        "operation_date": "",
        "amount": "",
        "currency": "MXN",
        "beneficiary": "",
        "account_source": "",
        "account_destination": "",
        "bank": "",
        "folio": "",
        "reference": "",
        "concept": "",
        "rfc": "",
        "curp": "",
        "uuid": "",
        "evidence": clean_space(evidence)[:1000],
        "extra": {},
    }


def payroll_record(document, page_number, text):
    folded = fold(text)
    if "NETO DEL RECIBO" not in folded or not ("CURP" in folded or "PERCEPCIONES" in folded):
        return None
    employee = first_group(
        r"\b\d{3}\s*-\s*([^|\n]{4,80}?)(?=\s+(?:Ejercicio|RFC|Periodo)|[|\n])", text
    )
    if not employee:
        employee = first_group(r"\b\d{3}\s*-\s*([A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ ]{3,60})", text)
    employee = clean_person_name(employee)
    rfcs = [re.sub(r"\s+", "", item).upper() for item in RFC.findall(text.upper())]
    rfc = next((value for value in rfcs if value != "MEZ850101AH7"), rfcs[-1] if rfcs else "")
    curp_match = CURP.search(text.upper())
    net = first_money(r"NETO\s+DEL\s+RECIBO\s*\$?\s*(\(?\s*\$?\s*\d[\d, ]*\.\d{2}\s*\)?)", folded)
    payment_date = parse_date_text(
        first_group(r"FECHA\s+PAGO\s*[:|]?\s*[^\d]*(\d{1,2}/(?:[A-Z]{3}|\d{1,2})/20\d{2})", folded)
    )
    uuid_match = UUID.search(text.upper())
    valid_id = (rfc and valid_embedded_date(rfc)) or (
        curp_match and valid_embedded_date(curp_match.group(1))
    )
    confidence = "alta" if employee and net and valid_id else "media"
    record = base_record("recibo_nomina", document, page_number, confidence, text[:1400])
    record.update(
        {
            "operation_date": payment_date,
            "amount": net,
            "beneficiary": employee,
            "rfc": rfc,
            "curp": curp_match.group(1).upper() if curp_match else "",
            "uuid": uuid_match.group(1).upper() if uuid_match else "",
            "concept": "Recibo de nómina",
            "extra": {
                "periodo": first_group(r"PERIODO\s+([^\n]{4,80})", folded),
                "puesto": first_group(r"PUESTO\s*:\s*([^\n]{3,80})", text),
                "departamento": first_group(r"DEPTO\s*:\s*([^\n]{3,80})", text),
            },
        }
    )
    discriminator = record["uuid"] or f"{record['curp']}|{record['operation_date']}|{net}"
    record["record_key"] = record_key(record["record_type"], document["id"], discriminator)
    return record


def cheque_payee(text):
    folded = fold(text)
    match = re.search(r"PAGUESE[^\n]{0,60}CHEQUE[^\n]{0,60}?(?:A|DE)\s*:\s*([^\n]*)", folded)
    candidates = re.findall(r"A\s+FAVOR\s+DE\s*:\s*\n?\s*([^\n]{3,100})", folded)
    if match:
        candidates.append(match.group(1))
        candidates.extend(folded[match.end() :].splitlines()[:4])
    for candidate in candidates:
        candidate = clean_space(candidate)
        if not candidate or "FECHA" in candidate:
            continue
        candidate = re.split(
            r"\s+(?:[A-Z+<>|\[\]]{0,12}\s*)?\d{1,3}(?:,\d{3})*\.\d{2}", candidate, maxsplit=1
        )[0]
        candidate = clean_space(candidate)
        if len(candidate) >= 4 and re.search(r"[A-Z]{3}", candidate):
            return candidate[:100]
    return ""


def cheque_record(document, page_number, text):
    folded = fold(text)
    payee = cheque_payee(text)
    if not payee:
        return None
    cheque = first_group(r"\bCH\s*[:;-]?\s*[-A-Z ]*?(\d{1,8})", folded) or first_group(
        r"CHEQUE\s+NUMERO\s+(\d{1,8})", folded
    )
    payee_position = re.search(r"PAGUESE[^\n]{0,100}CHEQUE", folded)
    amount = ""
    if payee_position:
        amount_match = MONEY.search(folded, payee_position.start(), min(len(folded), payee_position.start() + 500))
        if amount_match:
            amount = format(money_value(amount_match.group(1)), ".2f")
    if not amount:
        amount = first_money(r"CANTIDAD\s+DE[^$\d]{0,60}(\$?\s*\d[\d, ]*\.\d{2})", folded)
    account = first_group(r"(?:CTA|NO\.?\s+DE\s+CUENTA)\s*:\s*(\d[\d ]{7,20})", folded)
    concept = first_group(r"POR\s+CONCEPTO\s+DE\s*:\s*([^\n]{4,300})", folded)
    date_value = first_group(r"FECHA\s*:\s*([^\n]{5,40})", folded)
    record = base_record("cheque", document, page_number, "alta" if cheque and amount else "media", text[:1800])
    record.update(
        {
            "operation_date": date_value,
            "amount": amount,
            "beneficiary": payee,
            "account_source": re.sub(r"\D", "", account),
            "folio": cheque,
            "concept": concept,
        }
    )
    discriminator = f"cheque:{cheque}" if cheque else f"{payee}|{amount}|{concept[:80]}"
    record["record_key"] = record_key("cheque", document["id"], discriminator)
    return record


def transfer_record(document, page_number, text):
    folded = fold(text)
    if "NET CASH" not in folded or "FOLIO" not in folded:
        return None
    folio = first_group(r"FOLIO(?:\s+DEL\s+LOTE)?\s*:\s*([A-Z0-9-]{4,40})", folded)
    unique_folio = first_group(r"FOLIO\s+UNICO\s*:\s*([A-Z0-9-]{8,60})", folded)
    amount = first_money(r"IMPORTE\s+TOTAL\s*:\s*(\$?\s*\d[\d, ]*\.\d{2})", folded)
    if not amount:
        amount = first_money(r"CIFRA\s+DE\s+CONTROL\s*:\s*(\$?\s*\d[\d, ]*\.\d{2})", folded)
    if not (folio and (amount or unique_folio)):
        return None
    record = base_record("lote_transferencia", document, page_number, "alta" if amount else "media", text[:1800])
    record.update(
        {
            "operation_date": parse_date_text(
                first_group(r"FECHA\s+DE\s+(?:OPERACION|APLICACION)\s*:\s*([^\n]{5,30})", folded)
            ),
            "amount": amount,
            "account_source": re.sub(
                r"\D", "", first_group(r"CUENTA\s+DE\s+CARGO\s*:\s*(\d[\d ]{7,20})", folded)
            ),
            "bank": "BBVA",
            "folio": folio,
            "reference": unique_folio,
            "concept": first_group(r"DESCRIPCION\s*:\s*([^\n]{3,120})", folded),
            "extra": {
                "registros": first_group(r"NUMERO\s+DE\s+REGISTROS\s*:\s*(\d+)", folded),
                "estado": first_group(r"ESTADO\s*:\s*([^\n]{3,40})", folded),
            },
        }
    )
    record["record_key"] = record_key("lote_transferencia", document["id"], f"{folio}|{amount}")
    return record


def policy_record(document, page_number, text):
    folded = fold(text)
    if not ("POLIZAS DE PAGO FOLIO" in folded or re.search(r"\bORDEN\s+PAGO\b", folded)):
        return None
    folio = first_group(r"FOLIO\s*:\s*([A-Z0-9-]{5,30})", folded)
    if not folio:
        return None
    total = first_money(r"\bTOTAL\s*\$?\s*(\$?\s*\d[\d, ]*\.\d{2})", folded)
    beneficiary = first_group(r"AUXILIAR\s+([^\n]{3,100})", folded)
    beneficiary = clean_space(beneficiary.split("ESTATUS", 1)[0])
    concept = first_group(r"OBSERVACIONES\s+([^\n]{5,300})", folded)
    record = base_record("poliza_orden_pago", document, page_number, "alta" if total else "media", text[:1800])
    record.update(
        {
            "operation_date": parse_date_text(first_group(r"FECHA\s+TRAMITE\s*:\s*([^\n]{5,30})", folded)),
            "amount": total,
            "beneficiary": beneficiary,
            "folio": folio,
            "concept": concept,
        }
    )
    record["record_key"] = record_key("poliza_orden_pago", document["id"], folio)
    return record


def document_year(document):
    path_years = re.findall(r"20\d{2}", document.get("relative_path", ""))
    if path_years:
        return int(path_years[0])
    try:
        detected = json.loads(document.get("detected_dates") or "[]")
    except (TypeError, json.JSONDecodeError):
        detected = []
    years = Counter()
    for item in detected:
        if re.match(r"20\d{2}", item.get("date", "")):
            years[int(item["date"][:4])] += int(item.get("occurrences", 1))
    if years:
        return years.most_common(1)[0][0]
    for field in (document.get("date_min"), document.get("date_max")):
        if field and re.match(r"20\d{2}", field):
            return int(field[:4])
    return 2021


def bank_records(document, page_number, text):
    folded = fold(text)
    if "ESTADO DE CUENTA" not in folded or not ("SPEI" in folded or "REFERENCIA" in folded):
        return []
    starts = list(
        re.finditer(
            r"(?m)^\s*(\d{2})/([A-Z]{3})\s+\d{2}/[A-Z]{3}\s+([A-Z0-9]{2,4})\s+([^\n]{4,240})$",
            folded,
        )
    )
    records = []
    year = document_year(document)
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else min(len(folded), match.end() + 700)
        block = folded[match.start() : end]
        description = clean_space(match.group(4))
        amounts = [money_value(item) for item in MONEY.findall(match.group(4))]
        amounts = [item for item in amounts if item is not None]
        if not amounts:
            continue
        operation_date = normalized_date(match.group(1), MONTHS.get(match.group(2), 0), year)
        reference = first_group(r"REF\.?\s*([A-Z0-9 ]{4,30})", block)
        destination = re.search(r"(?<!\d)(\d{18,20})(?!\d)", block)
        bank = first_group(r"SPEI\s+(?:ENVIADO|RECIBIDO)\s+([A-Z]+)", description)
        direction = "salida" if "ENVIADO" in description else "entrada" if "RECIBIDO" in description else ""
        lines = [clean_space(line) for line in block.splitlines() if clean_space(line)]
        beneficiary = ""
        for line in reversed(lines[1:]):
            if (
                5 <= len(line) <= 100
                and re.fullmatch(r"[A-ZÑ ]+", line)
                and not any(word in line for word in ("BANCOMER", "INSTITUCION", "SPEI", "PAGINA"))
            ):
                beneficiary = line
                break
        record = base_record("movimiento_bancario", document, page_number, "alta" if reference else "media", block)
        record.update(
            {
                "operation_date": operation_date or "",
                "amount": format(amounts[0], ".2f"),
                "beneficiary": beneficiary,
                "account_destination": destination.group(1) if destination else "",
                "bank": bank,
                "reference": reference,
                "concept": description,
                "extra": {"codigo": match.group(3), "direccion": direction},
            }
        )
        discriminator = f"{operation_date}|{reference}|{amounts[0]}"
        if not reference:
            discriminator += f"|{page_number}|{match.start()}"
        record["record_key"] = record_key("movimiento_bancario", document["id"], discriminator)
        records.append(record)
    return records


def extract_records(document, page_number, text):
    records = []
    for extractor in (payroll_record, cheque_record, transfer_record, policy_record):
        record = extractor(document, page_number, text)
        if record:
            records.append(record)
    records.extend(bank_records(document, page_number, text))
    return records


def summarize_entity_counts(entities):
    return Counter(item["entity_type"] for item in entities)
