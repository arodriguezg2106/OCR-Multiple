"""Explainable multi-label classification; no model or expected-value lookup."""

import re
import unicodedata
from collections import Counter
from datetime import date

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

RULES = {
    "Cheque": {
        "filename": [
            (r"(^|\W)CH(?:EQUE)?[- _]", 10, "nombre con prefijo de cheque"),
            (r"CHEQUE", 8, "cheque en nombre"),
        ],
        "text": [
            (r"\bCHEQUE\b", 3, "palabra cheque"),
            (r"PAGUESE POR ESTE CHEQUE", 5, "leyenda bancaria de cheque"),
        ],
    },
    "Transferencia": {
        "filename": [
            (r"(^|\W)T[- _]?\d", 10, "nombre con prefijo de transferencia"),
            (r"TRANSFE|TRANFER", 10, "transferencia o abreviatura en nombre"),
        ],
        "text": [
            (r"TRANSFERENCIA (?:ELECTRONICA|BANCARIA)", 5, "leyenda de transferencia"),
            (r"\bSPEI\b", 4, "referencia SPEI"),
        ],
    },
    "Nómina": {
        "filename": [(r"NOMINA|RECIBOS? DE NOMINA", 10, "nómina en nombre")],
        "text": [
            (r"\bNOMINA\b", 4, "nómina en texto"),
            (r"PERCEPCIONES.{0,80}DEDUCCIONES", 5, "percepciones y deducciones"),
        ],
    },
    "Póliza": {
        "filename": [(r"POLIZA", 10, "póliza en nombre")],
        "text": [
            (r"\bPOLIZA(?: DE)? (?:EGRESOS|INGRESOS|DIARIO|CHEQUE)\b", 5, "encabezado de póliza"),
            (r"\bNUMERO DE POLIZA\b", 4, "número de póliza"),
        ],
    },
    "Balanza de comprobación": {
        "filename": [(r"BALANZA", 10, "balanza en nombre")],
        "text": [
            (r"BALANZA DE COMPROBACION", 8, "encabezado de balanza"),
            (r"SALDO INICIAL.{0,100}MOVIMIENTOS", 4, "columnas contables"),
        ],
    },
    "Finiquito": {
        "filename": [(r"FINIQUITO", 10, "finiquito en nombre")],
        "text": [
            (r"\bFINIQUITO\b", 6, "finiquito en texto"),
            (r"TERMINACION (?:DE LA )?RELACION LABORAL", 4, "terminación laboral"),
        ],
    },
    "Estado de cuenta": {
        "filename": [
            (r"ESTADO(?:S)? DE CUENTA", 10, "estado de cuenta en nombre"),
            (r"\bEDO\s*CUENTA|\bEDOCTA", 10, "abreviatura de estado de cuenta en nombre"),
        ],
        "text": [
            (r"ESTADO DE CUENTA", 6, "encabezado de estado de cuenta"),
            (r"SALDO ANTERIOR.{0,100}SALDO FINAL", 4, "saldos bancarios"),
        ],
    },
    "Factura o CFDI": {
        "filename": [(r"FACTURA|CFDI", 8, "factura o CFDI en nombre")],
        "text": [
            (r"FOLIO FISCAL", 5, "folio fiscal"),
            (r"COMPROBANTE FISCAL DIGITAL", 6, "encabezado CFDI"),
            (r"\bUUID\b", 3, "UUID"),
        ],
    },
    "Estado analítico del presupuesto": {
        "filename": [(r"ESTADO ANALITICO", 10, "estado analítico en nombre")],
        "text": [
            (
                r"ESTADO ANALITICO.{0,100}EJERCICIO.{0,100}PRESUPUESTO",
                8,
                "encabezado de estado analítico presupuestal",
            ),
        ],
    },
    "Cuenta pública": {
        "filename": [(r"CUENTA PUBLICA", 10, "cuenta pública en nombre")],
        "text": [(r"\bCUENTA PUBLICA\b", 6, "cuenta pública en texto")],
    },
    "Expediente de pago especial": {
        "filename": [
            (r"AGENTES MUNICIPALES", 10, "agentes municipales en nombre"),
            (r"PAGO DE LAUDO", 10, "pago de laudo en nombre"),
            (r"PAGO DIA DEL POLICIA", 10, "pago del Día del Policía en nombre"),
        ],
        "text": [],
    },
}

STOPWORDS = {
    "para",
    "como",
    "este",
    "esta",
    "estos",
    "estas",
    "desde",
    "hasta",
    "sobre",
    "entre",
    "donde",
    "fecha",
    "total",
    "pagina",
    "municipio",
    "ayuntamiento",
    "pesos",
    "centavos",
    "documento",
    "nombre",
    "numero",
    "cuenta",
    "concepto",
    "importe",
    "haber",
    "debe",
    "federal",
    "mexico",
    "veracruz",
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
}


def fold(value):
    value = unicodedata.normalize("NFKD", value)
    return "".join(c for c in value if not unicodedata.combining(c)).upper()


def folder_period(relative_path):
    first = relative_path.replace("\\", "/").split("/", 1)[0]
    match = re.match(r"\s*(\d{1,2})\s*[. _-]*\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)?", first)
    if not match:
        return None, first
    number = int(match.group(1))
    return (number if 1 <= number <= 12 else None), first


def classify(filename, text):
    sources = {"filename": fold(filename), "text": fold(text[:150_000])}
    scores = Counter()
    filename_scores = Counter()
    evidence = {}
    for kind, groups in RULES.items():
        found = []
        for source, rules in groups.items():
            for pattern, points, label in rules:
                if re.search(pattern, sources[source], re.DOTALL):
                    scores[kind] += points
                    if source == "filename":
                        filename_scores[kind] += points
                    found.append(label)
        if found:
            evidence[kind] = found
    ordered = scores.most_common()
    labels = [kind for kind, score in ordered if score >= 4]
    if not ordered or ordered[0][1] < 4:
        return "Otros / requiere revisión", "baja", labels, scores, evidence
    top_kind, top_score = ordered[0]
    if filename_scores:
        filename_ordered = filename_scores.most_common()
        top_kind, filename_score = filename_ordered[0]
        if len(filename_ordered) == 1 or filename_score > filename_ordered[1][1]:
            return top_kind, "alta", labels, scores, evidence
    margin = top_score - (ordered[1][1] if len(ordered) > 1 else 0)
    confidence = "alta" if top_score >= 8 and margin >= 3 else "media" if margin >= 2 else "baja"
    if confidence == "baja":
        top_kind = "Otros / requiere revisión"
    return top_kind, confidence, labels, scores, evidence


def extract_dates(text):
    folded = fold(text)
    values = Counter()
    for day, month, year in re.findall(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](20\d{2})(?!\d)", folded):
        try:
            value = date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            continue
        values[value] += 1
    month_names = "|".join(MONTHS)
    for day, month, year in re.findall(
        rf"(?<!\d)(\d{{1,2}})\s+DE\s+({month_names})\s+DE\s+(20\d{{2}})", folded, re.I
    ):
        try:
            value = date(int(year), MONTHS[month.lower()], int(day)).isoformat()
        except ValueError:
            continue
        values[value] += 1
    return values


def frequent_terms(text, limit=15):
    tokens = re.findall(r"[A-ZÁÉÍÓÚÜÑ][A-ZÁÉÍÓÚÜÑ]{3,}", text.upper())
    counts = Counter(
        token for token in tokens if fold(token).lower() not in STOPWORDS and not token.isdigit()
    )
    return counts.most_common(limit)
