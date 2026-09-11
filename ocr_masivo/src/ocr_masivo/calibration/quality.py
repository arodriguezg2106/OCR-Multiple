import math
import re
from datetime import date
from decimal import Decimal

MONTHS = {
    name: i
    for i, name in enumerate(
        "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split(), 1
    )
}
MONEY = r"(?:\$\s*)?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"
DATE = r"\b\d{1,2}/\d{1,2}/\d{4}\b"
ACCOUNT = r"\b\d+(?:\.\d+){3,}\b"
HEADERS = {
    "ayuntamiento",
    "municipio",
    "poliza",
    "póliza",
    "pólizas",
    "cuenta",
    "total",
    "fecha",
    "debe",
    "haber",
    "egresos",
    "concepto",
    "saldo",
    "presupuesto",
    "comprobación",
    "pago",
}
COMMON = HEADERS | {
    "de",
    "del",
    "la",
    "el",
    "por",
    "con",
    "enero",
    "febrero",
    "pesos",
    "veracruz",
    "servicios",
    "personales",
    "finiquito",
    "orden",
    "pagado",
    "ingresos",
}


def normalize(value):
    """Only layout whitespace and a literal currency symbol. Never substitute characters."""
    return re.sub(r"\s+", " ", value.replace("$", "")).strip()


def score_reading(words):
    tokens = [w["text"] for w in words if w["text"].strip()]
    text = " ".join(tokens)
    conf = sum(max(0, w.get("confidence") or 0) * max(1, len(w["text"])) for w in words)
    conf /= max(1, sum(len(w["text"]) for w in words))
    alpha = sum(c.isalnum() for c in text)
    lex = [t.lower().strip(".,:$()") for t in tokens]
    plausible = sum(t in COMMON or bool(re.fullmatch(r"\d[\d.,/:-]*", t)) for t in lex) / max(1, len(lex))
    headers = len(set(lex) & HEADERS)
    patterns = sum(len(re.findall(p, text)) for p in (MONEY, DATE, ACCOUNT, r"\b[A-Z]{1,6}\d{6,}\b"))
    eligible = [w for w in words if len(w["text"]) > 2 and "box" in w]
    horizontal = sum(w["box"][2] - w["box"][0] > w["box"][3] - w["box"][1] for w in eligible) / max(
        1, len(eligible)
    )
    # PSM 3 can read vertical blocks without physically correcting the page. Penalize those alternatives.
    score = (
        0.55 * conf
        + 8 * math.log1p(alpha)
        + 25 * plausible
        + 3 * min(headers, 10)
        + 2 * math.log1p(patterns)
        + 30 * horizontal
    )
    return dict(
        score=round(score, 4),
        confidence=round(conf, 3),
        alphanumeric=alpha,
        plausible_ratio=round(plausible, 3),
        headers=headers,
        valid_patterns=patterns,
        horizontal_word_ratio=round(horizontal, 3),
    )


def choose_orientation(alternatives, osd=None):
    ordered = sorted(alternatives, key=lambda a: a["score"], reverse=True)
    first, second = ordered[:2]
    margin = (first["score"] - second["score"]) / max(abs(first["score"]), 1)
    ambiguous = margin < 0.06 or first["confidence"] < 35 or first["alphanumeric"] < 15
    return dict(
        angle=first["angle"],
        margin=round(margin, 4),
        ambiguous=ambiguous,
        confidence="baja" if ambiguous else ("alta" if margin > 0.18 else "media"),
        method="OSD como señal + cuatro lecturas puntuadas sin valores esperados",
        osd=osd,
        alternatives=alternatives,
    )


def validate_field(kind, raw, confidence=None):
    value = normalize(raw)
    issues = []
    if not value:
        return ["sin_lectura"]
    if confidence is not None and confidence < 75:
        issues.append("baja_confianza")
    if kind == "importe":
        valid = bool(
            re.fullmatch(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}|\((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)", value)
        )
        if not valid:
            issues.append("formato_invalido")
        if re.search(r"[OoISBl]", value):
            issues.append("confusion_alfanumerica")
        if len(re.sub(r"\D", "", value)) > 12:
            issues.append("longitud_atipica")
    elif kind == "fecha":
        dates = re.findall(r"\b(\d{1,3})/(\d{1,2})/(\d{4})\b", value)
        written = re.findall(r"\b(\d{1,3})\s+de\s+([a-z]+)\s+(?:de|del)\s+(\d{4})\b", value.lower())
        try:
            for day, month, year in dates:
                date(int(year), int(month), int(day))
            for day, month, year in written:
                date(int(year), MONTHS[month], int(day))
            if (
                not dates
                and not written
                and not re.search(r"\b(" + "|".join(MONTHS) + r") de \d{4}\b", value.lower())
            ):
                issues.append("formato_invalido")
        except (ValueError, KeyError):
            issues.append("fecha_invalida")
    elif kind == "cuenta":
        if not (re.fullmatch(r"\d{10,18}", value) or re.fullmatch(r"\d{1,4}(?:\.\d{1,4}){3,9}", value)):
            issues.append("formato_invalido")
        if re.search(r"[OoIlSB]", value):
            issues.append("confusion_alfanumerica")
    elif kind == "folio":
        if not re.fullmatch(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*", value):
            issues.append("formato_invalido")
        if re.search(r"(?i)(?:[OISB]\d|\d[OISBl])", value) or value.startswith("0P"):
            issues.append("ambiguedad_alfanumerica")
    return issues


def compare_readings(kind, left, right, confidence=None):
    issues = validate_field(kind, left, confidence)
    if normalize(left) != normalize(right):
        return dict(
            classification="discrepante",
            requiere_revision=True,
            left_raw=left,
            right_raw=right,
            issues=issues + ["lecturas_distintas"],
        )
    if any(x in issues for x in ("formato_invalido", "fecha_invalida")):
        classification = "formato_invalido"
    elif "baja_confianza" in issues:
        classification = "baja_confianza"
    elif issues:
        classification = "requiere_revision"
    else:
        classification = "coincidente"
    return dict(
        classification=classification,
        requiere_revision=bool(issues),
        left_raw=left,
        right_raw=right,
        issues=issues,
    )


def check_total(total, parts):
    """Only use with explicitly identified total/components, never every number on a page."""
    try:

        def number(s):
            s = normalize(s).replace(",", "")
            return -Decimal(s[1:-1]) if s.startswith("(") else Decimal(s)

        return abs(number(total) - sum(number(p) for p in parts)) <= Decimal(".01")
    except Exception:
        return None
