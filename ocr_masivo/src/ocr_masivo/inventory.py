import hashlib
import os
from pathlib import Path

from .utils import sha256
from .validator import InvalidPDF, inspect_pdf


def discover(root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"No existe la carpeta de entrada: {root}")

    def walk_error(error):
        raise error

    for folder, dirs, files in os.walk(root, followlinks=False, onerror=walk_error):
        dirs[:] = (
            sorted(
                d
                for d in dirs
                if not (Path(folder) / d).is_symlink() and not os.path.isjunction(Path(folder) / d)
            )
            if hasattr(os.path, "isjunction")
            else sorted(d for d in dirs if not (Path(folder) / d).is_symlink())
        )
        for name in sorted(files):
            path = Path(folder) / name
            if path.suffix.lower() == ".pdf" and not path.is_symlink() and root in path.resolve().parents:
                yield path


def inventory(config, db, on_progress=None):
    rows = []
    for path in discover(config.entrada):
        relative = path.relative_to(config.entrada).as_posix()
        if on_progress:
            on_progress(len(rows) + 1, relative)
        key = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
        row = dict(
            id=key,
            archivo_original=str(path),
            ruta_relativa=relative,
            nombre=path.name,
            tamano=0,
            hash_original="",
            paginas_original=0,
            estado="pending",
            fecha_inicio="",
            fecha_fin="",
            duracion_segundos=0,
            codigo_salida=None,
            mensaje_error="",
            archivo_resultado="",
            archivo_texto="",
            hash_resultado="",
            hash_texto="",
            paginas_resultado=0,
            caracteres_extraidos=0,
            tamano_resultado=0,
        )
        try:
            row["tamano"] = path.stat().st_size
            row["hash_original"] = sha256(path)
            old = db.get(key)
            if old and old["hash_original"] == row["hash_original"]:
                row = old
                if row["estado"] == "processing":
                    row["estado"] = "pending"
            else:
                if old:
                    for field in ("archivo_resultado", "hash_resultado", "archivo_texto", "hash_texto"):
                        row[field] = old.get(field, "")
                row["paginas_original"] = inspect_pdf(path)
        except InvalidPDF as exc:
            row.update(estado=exc.state, mensaje_error=str(exc))
        except Exception as exc:
            row.update(estado="failed", mensaje_error=str(exc))
        db.save(row)
        rows.append(row)
    return rows
