from collections import Counter
from pathlib import Path

import pymupdf

from .classifier import classify, extract_dates, folder_period, frequent_terms
from .database import SCHEMA_VERSION, CatalogDatabase
from .source import load_ocr_rows

SUCCESS = {"completed", "already_completed"}


def coverage_label(pages, pages_with_text, characters):
    if not pages_with_text or not characters:
        return "sin_texto"
    ratio = pages_with_text / max(pages, 1)
    average = characters / max(pages, 1)
    if ratio >= 0.95 and average >= 100:
        return "amplia"
    if ratio >= 0.70 and average >= 40:
        return "parcial"
    return "baja"


def catalog_record(row, page_texts=None, warning=""):
    page_texts = page_texts or []
    text = "\n".join(text for _, text in page_texts)
    month_number, month_folder = folder_period(row["ruta_relativa"])
    primary, confidence, labels, scores, evidence = classify(row["nombre"], text)
    dates = extract_dates(text)
    ordered_dates = sorted(dates)
    pages = int(row.get("paginas_resultado") or row.get("paginas_original") or len(page_texts))
    chars = sum(len(value) for _, value in page_texts)
    pages_with_text = sum(bool(value.strip()) for _, value in page_texts)
    status = row.get("estado", "desconocido")
    if status not in SUCCESS and not warning:
        warning = row.get("mensaje_error", "OCR no completado")
    return {
        "id": row["id"],
        "result_hash": row.get("hash_resultado", ""),
        "relative_path": row["ruta_relativa"],
        "month_number": month_number,
        "month_folder": month_folder,
        "filename": row["nombre"],
        "original_path": row.get("archivo_original", ""),
        "pdf_path": row.get("archivo_resultado", ""),
        "ocr_status": status,
        "pages": pages,
        "text_characters": chars,
        "pages_with_text": pages_with_text,
        "coverage": coverage_label(pages, pages_with_text, chars),
        "primary_type": primary,
        "classification_confidence": confidence,
        "contained_types": labels,
        "classification_evidence": {
            kind: {"score": scores[kind], "signals": evidence[kind]} for kind in evidence
        },
        "date_min": ordered_dates[0] if ordered_dates else None,
        "date_max": ordered_dates[-1] if ordered_dates else None,
        "detected_dates": [{"date": value, "occurrences": dates[value]} for value in ordered_dates[:50]],
        "frequent_terms": [{"term": term, "occurrences": count} for term, count in frequent_terms(text)],
        "warning": warning[-2000:],
        "schema_version": SCHEMA_VERSION,
    }


def read_pdf(path):
    pages = []
    with pymupdf.open(path) as document:
        for number, page in enumerate(document, 1):
            pages.append((number, page.get_text("text", sort=True)))
    return pages


def build(ocr_database, output_database, force=False, progress=print):
    source_rows = load_ocr_rows(ocr_database)
    database = CatalogDatabase(output_database)
    totals = Counter()
    for position, row in enumerate(source_rows, 1):
        signature = (
            row.get("hash_resultado", "") if row.get("estado") in SUCCESS else f"status:{row.get('estado')}"
        )
        if not force and database.reusable(row["id"], signature):
            totals["reused"] += 1
            continue
        pages = None if force else database.cached_pages(row["id"], signature)
        warning = ""
        if pages is not None:
            totals["reclassified"] += 1
        elif row.get("estado") in SUCCESS:
            path = Path(row.get("archivo_resultado", ""))
            try:
                if not path.is_file():
                    raise FileNotFoundError(f"No existe el PDF OCR: {path}")
                pages = read_pdf(path)
                totals["analyzed"] += 1
            except (OSError, RuntimeError, ValueError) as exc:
                warning = f"No se pudo leer el PDF OCR: {exc}"
                totals["read_errors"] += 1
        else:
            pages = []
            totals["ocr_pending"] += 1
        record = catalog_record(row, pages, warning)
        record["result_hash"] = signature
        database.save(record, pages)
        if position == 1 or position % 10 == 0 or position == len(source_rows):
            progress(f"{position}/{len(source_rows)} · {row['ruta_relativa']}")
    totals["source_documents"] = len(source_rows)
    totals["catalog_documents"] = len(database.documents())
    return database, dict(totals)
