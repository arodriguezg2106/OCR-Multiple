from decimal import Decimal

from analisis_documental_asf.payments import confirmed_findings, duplicate_rows, parse_amount


def row(number, account, amount, status="numerico"):
    return {
        "excel_row": number,
        "account": account,
        "origin": "PARTICIP.",
        "date_text": "SEP",
        "month": "SEP",
        "amount_raw": str(amount),
        "amount": Decimal(amount),
        "amount_status": status,
        "concept": "Nómina",
    }


def test_parse_amount_recovers_malformed_thousands_separator():
    amount, status = parse_amount("93.198.98")
    assert amount == Decimal("93198.98")
    assert status == "recuperado_separadores"


def test_duplicate_rows_groups_exact_payment_rows():
    rows = [row(10, "398", "4251.00"), row(11, "398", "4251.00"), row(12, "399", "4251.00")]
    assert [[item["excel_row"] for item in group] for group in duplicate_rows(rows)] == [[10, 11]]


def test_confirmed_finding_for_policy_193_requires_document_evidence():
    rows = [row(395, "Pol. 193", "11577.00")]
    matches = [
        {
            **rows[0],
            "source_amount": Decimal("111577.00"),
            "matched_path": "9. Septiembre/POLIZA0193.pdf",
            "match_status": "vinculado",
        }
    ]
    findings = confirmed_findings(rows, matches)
    assert findings[0]["kind"] == "importe_incompleto"
    assert findings[0]["adjustment"] == Decimal("100000.00")
