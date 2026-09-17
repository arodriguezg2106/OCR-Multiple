"""Build and publish Phase 2 structured extraction."""

import csv
import html
import io
import json
import os
import re
import sqlite3
import tempfile
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from .extraction import extract_entities, extract_records

EXTRACTION_SCHEMA_VERSION = 1


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".phase2-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def csv_text(rows, fields):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return "\ufeff" + output.getvalue()


def connect_source(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def create_output(path):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.building")
    for candidate in (temporary, Path(str(temporary) + "-wal"), Path(str(temporary) + "-shm")):
        candidate.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY,
            document_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            entity_type TEXT NOT NULL,
            value TEXT NOT NULL,
            normalized_value TEXT NOT NULL,
            label TEXT NOT NULL,
            confidence TEXT NOT NULL,
            snippet TEXT NOT NULL
        );
        CREATE TABLE records (
            id INTEGER PRIMARY KEY,
            record_key TEXT UNIQUE NOT NULL,
            record_type TEXT NOT NULL,
            document_id TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            pdf_path TEXT,
            page_number INTEGER NOT NULL,
            confidence TEXT NOT NULL,
            operation_date TEXT,
            amount TEXT,
            currency TEXT,
            beneficiary TEXT,
            account_source TEXT,
            account_destination TEXT,
            bank TEXT,
            folio TEXT,
            reference TEXT,
            concept TEXT,
            rfc TEXT,
            curp TEXT,
            uuid TEXT,
            evidence TEXT NOT NULL,
            extra TEXT NOT NULL
        );
        CREATE INDEX entities_document ON entities(document_id, page_number);
        CREATE INDEX entities_type_value ON entities(entity_type, normalized_value);
        CREATE INDEX records_document ON records(document_id, page_number);
        CREATE INDEX records_type ON records(record_type);
        CREATE INDEX records_folio ON records(folio);
        CREATE INDEX records_reference ON records(reference);
        CREATE INDEX records_beneficiary ON records(beneficiary);
        """
    )
    return connection, temporary, path


def build(source_database, output_database, progress=print):
    source = connect_source(source_database)
    target, temporary, final = create_output(output_database)
    entity_count = 0
    record_count = 0
    records_by_key = {}
    try:
        documents = {
            row["id"]: dict(row)
            for row in source.execute(
                "SELECT id, relative_path, primary_type, month_number, date_min, date_max, detected_dates, pdf_path "
                "FROM documents"
            )
        }
        total_pages = source.execute("SELECT count(*) FROM pages").fetchone()[0]
        entity_sql = (
            "INSERT INTO entities(document_id,relative_path,page_number,entity_type,value,"
            "normalized_value,label,confidence,snippet) VALUES(?,?,?,?,?,?,?,?,?)"
        )
        record_fields = [
            "record_key",
            "record_type",
            "document_id",
            "relative_path",
            "pdf_path",
            "page_number",
            "confidence",
            "operation_date",
            "amount",
            "currency",
            "beneficiary",
            "account_source",
            "account_destination",
            "bank",
            "folio",
            "reference",
            "concept",
            "rfc",
            "curp",
            "uuid",
            "evidence",
            "extra",
        ]
        record_sql = f"INSERT OR IGNORE INTO records({','.join(record_fields)}) VALUES({','.join('?' for _ in record_fields)})"
        cursor = source.execute("SELECT document_id, page_number, text FROM pages ORDER BY id")
        with target:
            for position, page in enumerate(cursor, 1):
                document = documents[page["document_id"]]
                entities = extract_entities(page["text"])
                target.executemany(
                    entity_sql,
                    [
                        (
                            document["id"],
                            document["relative_path"],
                            page["page_number"],
                            item["entity_type"],
                            item["value"],
                            item["normalized_value"],
                            item["label"],
                            item["confidence"],
                            item["snippet"],
                        )
                        for item in entities
                    ],
                )
                records = extract_records(document, page["page_number"], page["text"])
                for item in records:
                    existing = records_by_key.get(item["record_key"])
                    if not existing:
                        item["extra"]["evidence_pages"] = [item["page_number"]]
                        records_by_key[item["record_key"]] = item
                        continue
                    pages = existing["extra"].setdefault("evidence_pages", [existing["page_number"]])
                    if item["page_number"] not in pages:
                        pages.append(item["page_number"])
                    for field in record_fields:
                        if field not in {"extra", "record_key"} and not existing.get(field) and item.get(field):
                            existing[field] = item[field]
                    if existing["confidence"] == "media" and item["confidence"] == "alta":
                        existing["confidence"] = "alta"
                entity_count += len(entities)
                if position == 1 or position % 250 == 0 or position == total_pages:
                    progress(f"{position}/{total_pages} páginas · {document['relative_path']}")
            target.executemany(
                record_sql,
                [
                    tuple(
                        json.dumps(item[name], ensure_ascii=False) if name == "extra" else item[name]
                        for name in record_fields
                    )
                    for item in records_by_key.values()
                ],
            )
            record_count = len(records_by_key)
            target.executemany(
                "INSERT INTO metadata VALUES(?,?)",
                [
                    ("schema_version", str(EXTRACTION_SCHEMA_VERSION)),
                    ("source_database", str(Path(source_database).resolve())),
                    ("documents", str(len(documents))),
                    ("pages", str(total_pages)),
                ],
            )
        target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        target.close()
        source.close()
        os.replace(temporary, final)
        Path(str(temporary) + "-wal").unlink(missing_ok=True)
        Path(str(temporary) + "-shm").unlink(missing_ok=True)
        return {"documents": len(documents), "pages": total_pages, "entities": entity_count, "records": record_count}
    except Exception:
        target.close()
        source.close()
        for candidate in (temporary, Path(str(temporary) + "-wal"), Path(str(temporary) + "-shm")):
            candidate.unlink(missing_ok=True)
        raise


def rows(connection, query):
    return [dict(row) for row in connection.execute(query)]


def local_link(path):
    try:
        return Path(path).resolve().as_uri() if path else ""
    except ValueError:
        return ""


def month_sort_key(item):
    match = re.match(r"\d+", item[0][0])
    return (int(match.group()) if match else 99, item[0][0], item[0][1])


def build_html(summary, record_summary, entity_summary, records):
    record_rows = "".join(
        f"<tr><td>{html.escape(item['record_type'])}</td><td>{item['count']}</td><td>{item['high']}</td><td>{item['medium']}</td></tr>"
        for item in record_summary
    )
    entity_rows = "".join(
        f"<tr><td>{html.escape(item['entity_type'])}</td><td>{item['count']}</td><td>{item['documents']}</td></tr>"
        for item in entity_summary
    )
    cards = []
    for item in records:
        searchable = " ".join(str(value or "") for value in item.values())
        fields = "".join(
            f"<dt>{html.escape(label)}</dt><dd>{html.escape(str(item[field] or '—'))}</dd>"
            for field, label in (
                ("operation_date", "Fecha"),
                ("amount", "Importe"),
                ("beneficiary", "Beneficiario / empleado"),
                ("folio", "Folio"),
                ("reference", "Referencia"),
                ("account_source", "Cuenta origen"),
                ("account_destination", "Cuenta destino"),
                ("rfc", "RFC"),
                ("curp", "CURP"),
                ("uuid", "UUID"),
                ("concept", "Concepto"),
            )
            if item[field]
        )
        cards.append(
            f'<article data-type="{html.escape(item["record_type"])}" data-confidence="{item["confidence"]}">'
            f'<h3>{html.escape(item["record_type"])}</h3><p>{html.escape(item["relative_path"])} · página {item["page_number"]}</p>'
            f'<p><a href="{local_link(item["pdf_path"])}#page={item["page_number"]}">Abrir PDF en la página</a></p>'
            f"<dl>{fields}</dl><details><summary>Evidencia OCR</summary><p>{html.escape(item['evidence'])}</p></details>"
            f'<span class="searchable">{html.escape(searchable)}</span></article>'
        )
    type_options = "".join(
        f'<option value="{html.escape(item["record_type"])}">{html.escape(item["record_type"])}</option>'
        for item in record_summary
    )
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Extracción documental ASF — Fase 2</title><style>
body{{font:15px system-ui;background:#edf2f5;color:#183241;max-width:1500px;margin:auto;padding:24px}}header,section,article{{background:white;border-radius:12px;padding:20px;margin:14px 0}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}}.card{{background:#174d6f;color:white;padding:18px;border-radius:10px;font-size:21px}}table{{border-collapse:collapse;width:100%}}th,td{{padding:9px;border-bottom:1px solid #d5dfe5;text-align:left}}.controls{{display:flex;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:2}}input,select{{padding:11px;min-width:230px;font:inherit}}dl{{display:grid;grid-template-columns:minmax(140px,220px) 1fr;gap:5px 14px}}dt{{font-weight:700}}dd{{margin:0}}.searchable{{display:none}}[hidden]{{display:none}}</style>
<header><h1>Extracción documental — Fase 2</h1><p>Datos candidatos con documento, página y evidencia OCR. Requieren validación antes de usarse como conclusión contable.</p><div class="cards"><div class="card">{summary['documents']}<small><br>PDF</small></div><div class="card">{summary['pages']}<small><br>páginas</small></div><div class="card">{summary['entities']}<small><br>entidades</small></div><div class="card">{summary['records']}<small><br>registros</small></div></div></header>
<section><h2>Registros estructurados</h2><table><tr><th>Tipo</th><th>Total</th><th>Alta</th><th>Media</th></tr>{record_rows}</table></section>
<section><h2>Entidades detectadas</h2><table><tr><th>Entidad</th><th>Ocurrencias únicas por página</th><th>Documentos</th></tr>{entity_rows}</table></section>
<section class="controls"><input id="q" type="search" placeholder="Buscar nombre, folio, RFC, cuenta…"><select id="type"><option value="">Todos los registros</option>{type_options}</select><select id="confidence"><option value="">Toda confianza</option><option>alta</option><option>media</option></select><strong id="count"></strong></section>
<main>{''.join(cards)}</main><script>const all=[...document.querySelectorAll('article')],q=document.querySelector('#q'),type=document.querySelector('#type'),confidence=document.querySelector('#confidence'),count=document.querySelector('#count');function filter(){{let query=q.value.toLocaleLowerCase(),n=0;all.forEach(x=>{{let show=(!query||x.textContent.toLocaleLowerCase().includes(query))&&(!type.value||x.dataset.type===type.value)&&(!confidence.value||x.dataset.confidence===confidence.value);x.hidden=!show;if(show)n++}});count.textContent=n+' visibles'}}[q,type,confidence].forEach(x=>x.addEventListener('input',filter));filter();</script></html>"""


def publish(database_path, output_dir, execution):
    output_dir = Path(output_dir).resolve()
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    entity_rows = rows(
        connection,
        "SELECT document_id,relative_path,page_number,entity_type,value,normalized_value,label,confidence,snippet FROM entities ORDER BY relative_path,page_number,entity_type",
    )
    record_rows = rows(
        connection,
        "SELECT record_type,document_id,relative_path,pdf_path,page_number,confidence,operation_date,amount,currency,beneficiary,account_source,account_destination,bank,folio,reference,concept,rfc,curp,uuid,evidence,extra FROM records ORDER BY relative_path,page_number,record_type",
    )
    entity_summary = rows(
        connection,
        "SELECT entity_type,count(*) count,count(DISTINCT document_id) documents FROM entities GROUP BY entity_type ORDER BY count(*) DESC",
    )
    record_summary = rows(
        connection,
        "SELECT record_type,count(*) count,sum(confidence='alta') high,sum(confidence='media') medium FROM records GROUP BY record_type ORDER BY count(*) DESC",
    )
    amount_summary = rows(
        connection,
        "SELECT record_type,count(*) registros,count(DISTINCT document_id) documentos,"
        "sum(amount<>'') con_importe,printf('%.2f',sum(CASE WHEN amount<>'' THEN CAST(amount AS REAL) ELSE 0 END)) suma_candidata "
        "FROM records GROUP BY record_type ORDER BY registros DESC",
    )
    recurrent = rows(
        connection,
        "SELECT entity_type,normalized_value,count(*) ocurrencias,count(DISTINCT document_id) documentos,"
        "min(relative_path) primer_documento FROM entities "
        "WHERE entity_type NOT IN ('importe','fecha') GROUP BY entity_type,normalized_value "
        "ORDER BY entity_type,ocurrencias DESC,normalized_value",
    )
    payroll_people = rows(
        connection,
        "SELECT coalesce(nullif(curp,''),nullif(rfc,''),beneficiary) identificador,"
        "max(beneficiary) beneficiario,max(rfc) rfc,max(curp) curp,count(*) recibos,"
        "count(DISTINCT document_id) documentos,min(nullif(operation_date,'')) fecha_inicial,"
        "max(nullif(operation_date,'')) fecha_final,sum(amount<>'') con_importe,"
        "printf('%.2f',sum(CASE WHEN amount<>'' THEN CAST(amount AS REAL) ELSE 0 END)) neto_candidato,"
        "sum(confidence='media') por_revisar FROM records WHERE record_type='recibo_nomina' "
        "AND coalesce(nullif(curp,''),nullif(rfc,''),beneficiary)<>'' "
        "GROUP BY identificador ORDER BY beneficiario,identificador",
    )
    connection.close()
    entity_fields = list(entity_rows[0]) if entity_rows else ["document_id"]
    record_fields = list(record_rows[0]) if record_rows else ["document_id"]
    atomic_text(output_dir / "datos_extraidos.csv", csv_text(entity_rows, entity_fields))
    atomic_text(output_dir / "registros_estructurados.csv", csv_text(record_rows, record_fields))
    review = [item for item in record_rows if item["confidence"] != "alta"]
    atomic_text(output_dir / "revision_fase2.csv", csv_text(review, record_fields))
    atomic_text(
        output_dir / "resumen_importes_por_tipo.csv",
        csv_text(amount_summary, list(amount_summary[0]) if amount_summary else ["record_type"]),
    )
    atomic_text(
        output_dir / "valores_recurrentes.csv",
        csv_text(recurrent, list(recurrent[0]) if recurrent else ["entity_type"]),
    )
    atomic_text(
        output_dir / "resumen_nomina_por_persona.csv",
        csv_text(payroll_people, list(payroll_people[0]) if payroll_people else ["identificador"]),
    )
    monthly = defaultdict(
        lambda: {
            "registros": 0,
            "confianza_alta": 0,
            "por_revisar": 0,
            "con_importe": 0,
            "suma_candidata": Decimal("0"),
        }
    )
    for item in record_rows:
        month = item["relative_path"].replace("\\", "/").split("/", 1)[0]
        key = (month, item["record_type"])
        monthly[key]["registros"] += 1
        monthly[key]["confianza_alta"] += item["confidence"] == "alta"
        monthly[key]["por_revisar"] += item["confidence"] != "alta"
        if item["amount"]:
            monthly[key]["con_importe"] += 1
            monthly[key]["suma_candidata"] += Decimal(item["amount"])
    monthly_rows = [
        {
            "carpeta_mes": month,
            "tipo_registro": record_type,
            **{key: str(value) if key == "suma_candidata" else value for key, value in values.items()},
        }
        for (month, record_type), values in sorted(monthly.items(), key=month_sort_key)
    ]
    atomic_text(
        output_dir / "resumen_registros_por_mes.csv",
        csv_text(monthly_rows, list(monthly_rows[0]) if monthly_rows else ["carpeta_mes"]),
    )
    summary = {
        **execution,
        "records_by_type": {item["record_type"]: item["count"] for item in record_summary},
        "entities_by_type": {item["entity_type"]: item["count"] for item in entity_summary},
        "records_for_review": len(review),
    }
    atomic_text(output_dir / "resumen_fase2.json", json.dumps(summary, ensure_ascii=False, indent=2))
    atomic_text(
        output_dir / "extraccion_documental.html",
        build_html(summary, record_summary, entity_summary, record_rows),
    )
    return summary
