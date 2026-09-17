from analisis_documental_asf.classifier import classify, extract_dates, folder_period


def test_folder_period():
    assert folder_period("9. Septiembre/POLIZA0155.pdf") == (9, "9. Septiembre")
    assert folder_period("Sin mes/documento.pdf") == (None, "Sin mes")


def test_primary_and_contained_types_are_explainable():
    primary, confidence, labels, scores, evidence = classify(
        "CH-144.pdf",
        "Póliza de egresos. Páguese por este cheque. Folio fiscal UUID comprobante fiscal digital.",
    )
    assert primary == "Cheque"
    assert confidence == "alta"
    assert {"Cheque", "Póliza", "Factura o CFDI"}.issubset(labels)
    assert scores["Cheque"] > scores["Póliza"]
    assert "nombre con prefijo de cheque" in evidence["Cheque"]


def test_uncertain_document_is_not_forced():
    result = classify("documento.pdf", "texto general sin encabezados")
    assert result[0] == "Otros / requiere revisión"
    assert result[1] == "baja"


def test_explicit_filename_wins_over_mixed_bundle_content():
    primary, confidence, labels, _, _ = classify(
        "TRANSFE -38028_0001.pdf",
        "Póliza de pago de nómina con cheque y comprobante fiscal digital",
    )
    assert primary == "Transferencia"
    assert confidence == "alta"
    assert {"Transferencia", "Nómina", "Factura o CFDI"}.issubset(labels)


def test_budget_statement_and_abbreviated_bank_statement():
    assert classify("Estado analítico septiembre.pdf", "presupuesto de egresos")[0] == (
        "Estado analítico del presupuesto"
    )
    assert classify("EDOCTADIG AGOSTO.pdf", "saldo anterior y saldo final")[0] == (
        "Estado de cuenta"
    )


def test_named_special_payment_bundle_keeps_contained_types():
    primary, confidence, labels, _, _ = classify(
        "Pago Dia del policia, otros bancos.pdf",
        "Póliza de egresos, nómina y cheque",
    )
    assert primary == "Expediente de pago especial"
    assert confidence == "alta"
    assert {"Expediente de pago especial", "Póliza", "Nómina"}.issubset(labels)


def test_dates_only_include_real_calendar_dates():
    dates = extract_dates("29/02/2024, 31/02/2024 y 3 de marzo de 2021; repetida 03-03-2021")
    assert dates["2024-02-29"] == 1
    assert dates["2021-03-03"] == 2
    assert len(dates) == 2
