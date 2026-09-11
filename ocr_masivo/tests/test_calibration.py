import json
from pathlib import Path

import pytest
from PIL import Image

from ocr_masivo.calibration.engine import region_words, render, transform_box
from ocr_masivo.calibration.fields import extract_value
from ocr_masivo.calibration.quality import (
    check_total,
    choose_orientation,
    compare_readings,
    normalize,
    score_reading,
    validate_field,
)
from ocr_masivo.utils import sha256


def test_four_orientations():
    alternatives = [
        dict(angle=a, score=s, confidence=90, alphanumeric=100)
        for a, s in zip((0, 90, 180, 270), (30, 130, 20, 35), strict=True)
    ]
    result = choose_orientation(alternatives)
    assert result["angle"] == 90
    assert not result["ambiguous"]


def test_ambiguous_orientation():
    alternatives = [
        dict(angle=a, score=s, confidence=80, alphanumeric=100)
        for a, s in zip((0, 90, 180, 270), (100, 99, 98, 97), strict=True)
    ]
    assert choose_orientation(alternatives)["ambiguous"]


def test_vertical_words_do_not_pass_as_upright():
    common = dict(text="ayuntamiento", confidence=90)
    horizontal = score_reading([{**common, "box": [0, 0, 120, 20]}])
    vertical = score_reading([{**common, "box": [0, 0, 20, 120]}])
    assert horizontal["score"] > vertical["score"]


def test_rotated_render_preserves_original(tmp_path):
    import pymupdf

    source = tmp_path / "original.pdf"
    with pymupdf.open() as doc:
        doc.new_page(width=200, height=300).insert_text((20, 40), "Prueba 123")
        doc.save(source)
    digest = sha256(source)
    target = tmp_path / "rotation.png"
    render(source, 0, target, 72, 90)
    with Image.open(target) as image:
        assert image.size == (300, 200)
    assert sha256(source) == digest
    assert not list(tmp_path.glob("*.partial.png"))


@pytest.mark.parametrize("value", ["23,319.00", "45,999.00", "$23,319.00", "(23,319.00)", "-23,319.00"])
def test_valid_amounts(value):
    assert not validate_field("importe", value)


@pytest.mark.parametrize("value", ["23,31.00", "23,319.0", "O3,319.00", "23.319,00"])
def test_invalid_amounts(value):
    assert validate_field("importe", value)


def test_zero_preservation_and_raw_retention():
    raw = " CTA: 00123456789 "
    result = extract_value("cuenta", raw)
    assert result["value"] == "00123456789"
    assert result["raw_region"] == raw
    assert normalize("00123456789") == "00123456789"


def test_o_zero_ambiguity():
    result = compare_readings("folio", "OP202101000143", "0P202101000143", 99)
    assert result["classification"] == "discrepante"
    assert result["requiere_revision"]
    assert result["right_raw"] == "0P202101000143"
    assert "ambiguedad_alfanumerica" in validate_field("folio", "0P202101000143")


def test_matching_but_ambiguous_needs_review():
    result = compare_readings("folio", "0P202101000143", "0P202101000143", 99)
    assert result["classification"] == "requiere_revision"


def test_bad_format_and_low_confidence():
    assert compare_readings("importe", "123.4", "123.4", 99)["classification"] == "formato_invalido"
    assert compare_readings("importe", "123.40", "123.40", 30)["classification"] == "baja_confianza"
    assert compare_readings("importe", "123.40", "123.40", 95)["classification"] == "coincidente"


def test_dates_and_totals():
    assert not validate_field("fecha", "29/02/2024")
    assert "fecha_invalida" in validate_field("fecha", "29/02/2023")
    assert "fecha_invalida" in validate_field("fecha", "321 de marzo de 2018")
    assert check_total("46,638.00", ["23,319.00", "23,319.00"])
    assert not check_total("46,638.00", ["523,319.00", "23,319.00"])


def test_roi_transform_and_no_neighbor_substitution():
    box = [0.1, 0.2, 0.3, 0.4]
    restored = transform_box(transform_box(box, 90), 270)
    assert restored == pytest.approx(box)
    record = dict(
        size=[100, 100],
        words=[dict(text="wrong", box=[10, 10, 20, 20]), dict(text="correct", box=[70, 70, 80, 80])],
    )
    assert [w["text"] for w in region_words(record, [0, 0, 0.3, 0.3])] == ["wrong"]


def test_regions_cover_exactly_frozen_sixty():
    import ocr_masivo.calibration.fields as module

    regions = json.loads(Path(module.__file__).with_name("regions.json").read_text(encoding="utf-8"))
    assert set(regions["boxes"]) == {str(i) for i in range(1, 61)}
    assert len(regions["reference_angles"]) == 12
    assert all(0 <= v <= 1 for box in regions["boxes"].values() for v in box)


def test_csv_roundtrip_retains_raw_text_and_leading_zeros(tmp_path):
    import csv

    from ocr_masivo.calibration.report import field_record, write_csv

    raw = '00123, "observación"\nsegunda línea'
    row = field_record("B", "00123", "00123", "cuenta", raw_region=raw)
    path = tmp_path / "resultados.csv"
    write_csv(path, [row])
    with path.open(encoding="utf-8-sig", newline="") as stream:
        recovered = list(csv.DictReader(stream))
    assert recovered[0]["raw_value"] == "00123"
    assert recovered[0]["raw_region"] == raw
    assert recovered[0]["expected"] == "00123"
    assert recovered[0]["exact"] == "True"


def test_report_generates_navigable_html_and_all_exports(tmp_path):
    from ocr_masivo.calibration.report import publish, summarize

    records = []
    for profile in "ABCD":
        for idx, kind in enumerate(("importe", "fecha", "cuenta", "folio"), 1):
            records.append(
                dict(
                    profile=profile,
                    id=idx,
                    kind=kind,
                    exact=True,
                    page_id="p01",
                    document="prueba.pdf",
                    page=1,
                    label="Campo <original>",
                    expected="00123",
                    evidence="evidence/f01.png",
                    orientation=0,
                    psm=3,
                    raw_value="00123",
                    normalized="00123",
                    raw_region="<dato>00123",
                    confidence=90,
                    error_type="sin_error",
                    validators=[],
                    requires_review=False,
                    comparison={"classification": "coincidente"},
                    method="prueba",
                )
            )
    times = {p: dict(total_seconds=12, seconds_per_page=1) for p in "ABCD"}
    summary = summarize(records, times, dict.fromkeys("ABCD", 12))
    publish(tmp_path, records, summary, [])
    document = (tmp_path / "informe_precision_v2.html").read_text(encoding="utf-8")
    assert "Campo &lt;original&gt;" in document
    assert "&lt;dato&gt;00123" in document
    assert document.count("<article>") == 4
    for name in (
        "resultados_detallados.csv",
        "comparacion_perfiles.csv",
        "orientaciones_detectadas.csv",
        "errores_no_resueltos.csv",
        "resumen.json",
    ):
        assert (tmp_path / name).is_file()
        assert name in document
