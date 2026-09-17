import sqlite3

from analisis_documental_asf.extraction import document_year, extract_entities, extract_records
from analisis_documental_asf.phase2 import build, publish

DOCUMENT = {
    "id": "doc-1",
    "relative_path": "1. Enero/Nomina.pdf",
    "primary_type": "Nómina",
    "month_number": 1,
    "date_min": "2021-01-01",
    "date_max": "2021-01-31",
    "detected_dates": '[{"date":"2021-01-15","occurrences":10}]',
    "pdf_path": "C:/ocr/Nomina.pdf",
}


PAYROLL = """
001 - Mier Acolt Jorge Alberto
RFC: MIAJ691018L26 Periodo 21 Quincenal 01/Nov/2021 -15/Nov/2021
CURP: MIAJ691018HVZRCROO Fecha Pago: 15/Nov/2021
Puesto: Presidente Municipal
Percepciones Deducciones
Neto del recibo $ 42,739.60
Folio Fiscal UUID: CDC5B86B-9D12-4E4C-A009-BA9DBE50D3C0
"""


def test_entities_are_normalized_and_keep_evidence():
    values = {(item["entity_type"], item["normalized_value"]): item for item in extract_entities(PAYROLL)}
    assert ("rfc", "MIAJ691018L26") in values
    assert ("curp", "MIAJ691018HVZRCROO") in values
    assert ("uuid", "CDC5B86B-9D12-4E4C-A009-BA9DBE50D3C0") in values
    assert values[("importe", "42739.60")]["label"] == "neto del recibo"
    assert "Neto del recibo" in values[("importe", "42739.60")]["snippet"]


def test_payroll_and_transfer_records_have_traceable_fields():
    payroll = extract_records(DOCUMENT, 1, PAYROLL)
    assert len(payroll) == 1
    assert payroll[0]["record_type"] == "recibo_nomina"
    assert payroll[0]["beneficiary"] == "Mier Acolt Jorge Alberto"
    assert payroll[0]["amount"] == "42739.60"
    transfer = """
BBVA Net Cash - Nómina dispersión
Cuenta de cargo: 0116257119
Descripción: COMPL DIA EMPLEADO
Fecha de operación: 26/10/2021
Folio del lote: 593540 Folio único: IPF5202110261629460071807020
Importe total: 20,229.01
"""
    records = extract_records(DOCUMENT, 2, transfer)
    assert len(records) == 1
    assert records[0]["folio"] == "593540"
    assert records[0]["amount"] == "20229.01"
    assert records[0]["account_source"] == "0116257119"


def test_cheque_and_bank_movement():
    cheque = """
PAGUESE ESTE CHEQUE A: ARTURO AMAYA GUTIÉRREZ $ 4,251.00
FECHA: 30 de julio de 2021
CTA: 00116257119 CH: 274-PARTICIPACIONES 2021
POR CONCEPTO DE: PAGO MENSUAL DE AGENTE MUNICIPAL
"""
    record = extract_records(DOCUMENT, 3, cheque)[0]
    assert record["record_type"] == "cheque"
    assert record["folio"] == "274"
    assert record["amount"] == "4251.00"
    duplicate = extract_records(
        DOCUMENT,
        4,
        "Páguese por este cheque a la orden de:\nARTURO AMAYA GUTIÉRREZ $ 4,251.00\nCH: 274",
    )[0]
    assert duplicate["record_key"] == record["record_key"]
    filename_document = dict(DOCUMENT, relative_path="11. Noviembre/CH-428.pdf")
    filename_folio = extract_records(
        filename_document,
        6,
        "Sirva pagar la orden a favor de:\nJUAN CARLOS HERNANDEZ PEREZ\nCantidad $ 4,122.66",
    )[0]
    assert filename_folio["folio"] == "428"
    statement = """
Estado de Cuenta
12/MAR 12/MAR T17 SPEI ENVIADO SANTANDER 39,986.20
0011519PAGO NOMINA Ref. 0000656247 014
00014840606003728489
ANA PAULINA MARTINEZ MURGUIA
"""
    movement = extract_records(DOCUMENT, 5, statement)[0]
    assert movement["record_type"] == "movimiento_bancario"
    assert movement["operation_date"] == "2021-03-12"
    assert movement["amount"] == "39986.20"
    assert movement["reference"].startswith("0000656247")


def test_policy_uses_repeated_ledger_amount_and_folder_month_date():
    policy = """
PÓLIZAS DE PAGO FOLIO: P202107000161
Fecha de impresión: 09/08/2021
EG202107310 CH-274 PAGO POR 30/07/2021 Remuneraciones (ARTURO AMAYA GUTIERREZ) $4,251.00
EG202107310 CH-274 PAGO POR 30/07/2021 Retribuciones (Pagado) $4,251.00
PARTICIPACIONES 2021 (ARTURO AMAYA GUTIERREZ) $4,251.00
TOTAL $8,502.00 $8,502.00
"""
    document = dict(DOCUMENT, month_number=7)
    record = next(item for item in extract_records(document, 1, policy) if item["record_type"] == "poliza_orden_pago")
    assert record["amount"] == "4251.00"
    assert record["operation_date"] == "2021-07-30"
    assert record["beneficiary"] == "ARTURO AMAYA GUTIERREZ"
    assert record["extra"]["amount_method"] == "repeated_ledger_amount"
    assert record["confidence"] == "media"


def test_bank_year_uses_dominant_document_dates():
    document = dict(DOCUMENT, date_min="2017-01-01")
    assert document_year(document) == 2021


def test_phase2_database_and_reports(tmp_path):
    source = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE documents (
                id TEXT PRIMARY KEY, relative_path TEXT, primary_type TEXT, month_number INTEGER,
                date_min TEXT, date_max TEXT, detected_dates TEXT, pdf_path TEXT
            );
            CREATE TABLE pages (
                id INTEGER PRIMARY KEY, document_id TEXT, page_number INTEGER, text TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)",
            tuple(DOCUMENT.values()),
        )
        connection.execute("INSERT INTO pages VALUES(1,'doc-1',1,?)", (PAYROLL,))
    output = tmp_path / "results"
    execution = build(source, output / "extraccion_fase2.sqlite3", progress=lambda _: None)
    summary = publish(output / "extraccion_fase2.sqlite3", output, execution)
    assert summary["documents"] == 1 and summary["records"] == 1
    assert summary["records_by_type"] == {"recibo_nomina": 1}
    assert (output / "datos_extraidos.csv").is_file()
    assert (output / "registros_estructurados.csv").is_file()
    assert (output / "resumen_nomina_por_persona.csv").is_file()
    assert (output / "resumen_registros_por_mes.csv").is_file()
    report = (output / "extraccion_documental.html").read_text(encoding="utf-8")
    assert "Mier Acolt Jorge Alberto" in report
