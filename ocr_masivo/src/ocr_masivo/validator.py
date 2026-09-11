from pathlib import Path

import pikepdf
import pymupdf


class InvalidPDF(Exception):
    def __init__(self, message, state="validation_failed"):
        super().__init__(message)
        self.state = state


def inspect_pdf(path):
    try:
        with pikepdf.open(path) as pdf:
            if pdf.is_encrypted:
                raise InvalidPDF("PDF cifrado o protegido; requiere revisión.", "encrypted")
            for obj in pdf.objects:
                if isinstance(obj, pikepdf.Dictionary):
                    if obj.get("/Type") == pikepdf.Name("/Sig") or "/ByteRange" in obj:
                        raise InvalidPDF("Firma digital detectada; original conservado.", "signed")
                    if obj.get("/FT") == pikepdf.Name("/Sig") and obj.get("/V") is not None:
                        raise InvalidPDF("Campo de firma digital detectado.", "signed")
        with pymupdf.open(path) as doc:
            if doc.is_repaired or doc.page_count < 1:
                raise InvalidPDF("PDF dañado o sin páginas.")
            return doc.page_count
    except pikepdf.PasswordError as exc:
        raise InvalidPDF("PDF cifrado; requiere revisión.", "encrypted") from exc
    except InvalidPDF:
        raise
    except Exception as exc:
        raise InvalidPDF(f"No se puede abrir el PDF: {exc}") from exc


def validate_result(path, pages):
    if not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise InvalidPDF("El resultado no existe o está vacío.")
    count = inspect_pdf(path)
    if count != pages:
        raise InvalidPDF(f"Número de páginas distinto: original {pages}, resultado {count}.")
    try:
        with pymupdf.open(path) as doc:
            chars = sum(len(page.get_text()) for page in doc)
    except Exception as exc:
        raise InvalidPDF(f"No se puede extraer texto: {exc}") from exc
    return count, chars


def extract_text(pdf, target):
    with pymupdf.open(pdf) as doc, target.open("w", encoding="utf-8", newline="") as out:
        for index, page in enumerate(doc):
            if index:
                out.write("\n\f\n")
            out.write(page.get_text())
