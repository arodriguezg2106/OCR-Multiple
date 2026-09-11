"""Genera un buscador HTML sin red, con referencias al PDF y página exacta."""

import argparse
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

import pymupdf

from ocr_masivo.database import Database
from ocr_masivo.processor import SUCCESSES
from ocr_masivo.utils import atomic_text, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destino", type=Path)
    args = parser.parse_args()
    root = args.destino.resolve()
    db = Database(root / "logs" / "estado.sqlite3")
    rows = [r for r in db.rows() if r["estado"] in SUCCESSES]
    cards = []
    metrics = []
    for row in rows:
        intact = sha256(row["archivo_original"]) == row["hash_original"]
        warnings = {}
        logfile = root / "logs" / (row["id"] + ".stderr.log")
        if logfile.exists():
            for line in logfile.read_text(encoding="utf-8", errors="replace").splitlines():
                match = re.match(r"\s*(\d+)\s+", line)
                if match and any(
                    token in line.lower()
                    for token in ("poor ocr", "confidence too low", "timeout", "timed out", "skipping")
                ):
                    warnings.setdefault(int(match[1]), []).append(line.strip())
        before = []
        with pymupdf.open(row["archivo_original"]) as original:
            before = [len(page.get_text()) for page in original]
        with pymupdf.open(row["archivo_resultado"]) as doc:
            counts = []
            for index, page in enumerate(doc):
                text = page.get_text()
                counts.append(len(text))
                links = []
                for label, key in (
                    ("Ver original", "archivo_original"),
                    ("Ver PDF OCR", "archivo_resultado"),
                ):
                    url = quote(os.path.relpath(row[key], root).replace(os.sep, "/")) + f"#page={index + 1}"
                    links.append(f'<a href="{url}" target="_blank" rel="noopener">{label}</a>')
                title = html.escape(row["ruta_relativa"])
                warning = ""
                if index + 1 in warnings:
                    warning = '<p class="warning">Revisar esta página: el motor señaló calidad, orientación o límites de OCR.</p>'
                cards.append(
                    f"<article><h2>{title} · página {index + 1}</h2><nav>{' · '.join(links)}</nav>"
                    f"<p>{len(text):,} caracteres extraídos · Original "
                    f"{'intacto' if intact else 'CAMBIADO'}</p><details><summary>SHA-256 del original</summary>"
                    f"<code>{row['hash_original']}</code></details>{warning}<pre>{html.escape(text)}</pre></article>"
                )
            metrics.append(
                {
                    "archivo": row["ruta_relativa"],
                    "paginas": len(doc),
                    "original_intacto": intact,
                    "paginas_con_texto_antes": sum(n > 0 for n in before),
                    "paginas_con_texto_despues": sum(n > 0 for n in counts),
                    "caracteres": sum(counts),
                    "paginas_sin_texto": [i + 1 for i, n in enumerate(counts) if not n],
                    "advertencias_por_pagina": warnings,
                }
            )
    document = (
        """<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prueba de trazabilidad — OCR Masivo Local</title>
<style>body{font:16px system-ui;background:#f3f5f8;color:#172338;max-width:1100px;margin:30px auto;padding:20px}
header,article{background:white;padding:24px;border-radius:12px;margin-bottom:18px}header{position:sticky;top:0;border:1px solid #bccad9}
h1{font-size:25px}h2{font-size:18px}input{font:inherit;padding:12px;width:90%;border:1px solid #64748b;border-radius:6px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.5 monospace}a{color:#1857a5}code{overflow-wrap:anywhere}
.note{font-size:14px;color:#475569}.warning{padding:12px;background:#fff0ce;color:#704600}[hidden]{display:none}</style>
<header><h1>Prueba de trazabilidad contable</h1>
<p>Busque un folio, cuenta, concepto, fecha o importe y abra la página original para contrastarlo.</p>
<input id="search" type="search" placeholder="Buscar texto en las páginas…" aria-label="Buscar texto">
<p id="count"></p><p class="note">Funciona localmente, sin conexiones de red. Las coincidencias no prueban una relación contable.
El OCR puede confundir cifras y columnas; confirme siempre en la imagen original.</p></header>
"""
        + "\n".join(cards)
        + """
<script>const input=document.querySelector('#search'),cards=[...document.querySelectorAll('article')];
const norm=s=>s.normalize('NFD').replace(/[\\u0300-\\u036f]/g,'').toLowerCase();
const texts=cards.map(c=>norm(c.textContent));function filter(){const q=norm(input.value.trim());let n=0;
cards.forEach((c,i)=>{c.hidden=!texts[i].includes(q);if(!c.hidden)n++});
document.querySelector('#count').textContent=n+' de '+cards.length+' páginas visibles';}
input.addEventListener('input',filter);filter();</script></html>"""
    )
    atomic_text(root / "explorador_trazabilidad.html", document)
    atomic_text(root / "logs" / "evaluacion_ocr.json", json.dumps(metrics, indent=2, ensure_ascii=False))
    print(json.dumps(metrics, ensure_ascii=True))


if __name__ == "__main__":
    main()
