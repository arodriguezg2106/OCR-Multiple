from analisis_documental_asf.phase3 import normalize_date, normalize_name, reconcile


def record(key, record_type, *, document="doc-1", page=1, amount="4251.00", **values):
    item = {
        "record_key": key,
        "record_type": record_type,
        "document_id": document,
        "relative_path": "7. Julio/CH-274.pdf",
        "pdf_path": "C:/ocr/CH-274.pdf",
        "page_number": page,
        "confidence": "alta",
        "operation_date": "2021-07-30",
        "amount": amount,
        "beneficiary": "ARTURO AMAYA GUTIERREZ",
        "folio": "",
        "reference": "",
        "concept": "Pago mensual",
        "evidence": "",
        "extra": "{}",
    }
    item.update(values)
    return item


def test_normalizes_written_dates_and_names():
    assert normalize_date("Fecha: 30 de julio de 2021") == "2021-07-30"
    assert normalize_date("30/07/21") == "2021-07-30"
    assert normalize_name("MUNICIPIO DE EMILIANO ZAPATA, VER.") == ""
    assert normalize_name("Gutiérrez Arturo Amaya") == "GUTIERREZ ARTURO AMAYA"


def test_builds_a_complete_trace_from_strong_evidence():
    records = [
        record("policy", "poliza_orden_pago", page=1, folio="P202107000161", evidence="PAGO CH-274"),
        record(
            "check",
            "cheque",
            page=4,
            folio="274",
            operation_date="30 DE JULIO DE 2021",
        ),
        record("transfer", "lote_transferencia", page=5, folio="274", beneficiary=""),
        record(
            "bank",
            "movimiento_bancario",
            document="statement",
            page=2,
            folio="",
            reference="0000656247 014",
        ),
    ]
    cases, links = reconcile(records)
    assert len(cases) == 1
    assert cases[0]["record_count"] == 4
    assert cases[0]["status"] == "conciliado_banco"
    assert cases[0]["amount"] == "4251.00"
    assert len([item for item in links if item["status"] == "aceptado"]) >= 3


def test_ambiguous_equal_amounts_are_not_merged():
    records = [
        record("check-a", "cheque", document="a", folio="101"),
        record("check-b", "cheque", document="b", folio="102"),
        record("bank", "movimiento_bancario", document="statement", beneficiary=""),
    ]
    cases, links = reconcile(records)
    assert len(cases) == 3
    assert not [item for item in links if item["status"] == "aceptado"]
    assert [item for item in links if item["status"] == "sugerido"]
