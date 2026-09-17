# Análisis Documental ASF

Proyecto local separado de OCR Masivo. La fase 1 construye una relación navegable de los PDF por carpeta, mes y tipo documental. Lee la capa de texto de los PDF con OCR y nunca modifica originales ni resultados.

## Resultados

- `catalogo_documentos.csv`: una fila por PDF.
- `resumen_por_mes.csv`: documentos, páginas, cobertura y revisiones por carpeta mensual.
- `resumen_por_tipo.csv`: clasificación principal y niveles de confianza.
- `documentos_sin_clasificar.csv`: casos sin evidencia suficiente o con empate.
- `documentos_con_advertencias.csv`: OCR fallido o PDF que no se pudo leer.
- `indice_documental.sqlite3`: texto por página e índice FTS5 para búsquedas y la futura trazabilidad.
- `catalogo_documental.html`: informe local con filtros y enlaces a los PDF.

La clasificación es explicable y multietiqueta. El tipo principal resume el expediente; `tipos_contenidos` registra componentes detectados, por ejemplo póliza, cheque y CFDI dentro del mismo PDF. Los casos dudosos no se fuerzan y quedan para revisión.

La primera aplicación al lote de 2021 catalogó 538 PDF y 13,729 páginas sin advertencias. Consulte el [resultado agregado de la fase 1](docs/RESULTADOS_FASE_1.md). Los informes detallados permanecen sólo en este equipo dentro de `resultados`.

La fase 2 extrae importes, fechas, folios, referencias, cuentas, CLABE, RFC, CURP, UUID, beneficiarios y conceptos. Construye registros de recibos de nómina, cheques, pólizas u órdenes de pago, lotes de transferencia y movimientos bancarios. Cada registro conserva la página y el fragmento OCR utilizado como evidencia. Consulte los [resultados agregados de la fase 2](docs/RESULTADOS_FASE_2.md).

La captura también concilia señales repetidas dentro del mismo expediente: puede completar nombres por consenso de RFC o CURP, recuperar folios desde nombres de archivo y contrastar importes de póliza con cheques o transferencias. Los campos completados se identifican en `extra`; una inferencia sin corroboración permanece con confianza media.

La fase 3 relaciona pólizas, cheques, lotes de transferencia, recibos de nómina y movimientos bancarios. Consolida las representaciones de un mismo pago, conserva la regla que justificó cada vínculo y separa las coincidencias ambiguas. Consulte los [resultados agregados de la fase 3](docs/RESULTADOS_FASE_3.md).

## Ejecutar

Desde PowerShell:

```powershell
cd D:\arodriguezg\Downloads\OCR_PDFS\IA-ASF\analisis_documental_asf
.\ejecutar_catalogo.ps1
```

El primer recorrido extrae texto de todos los PDF terminados. Las ejecuciones posteriores reutilizan los documentos cuyo hash no cambió. Para una instalación independiente:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
```

También puede indicar otras rutas:

```powershell
.\ejecutar_catalogo.ps1 -BaseOCR 'D:\Lote\logs\estado.sqlite3' -Salida 'D:\Catalogo'
```

Para ejecutar la fase 2 después de construir el catálogo:

```powershell
.\ejecutar_fase2.ps1
```

Los resultados se guardan en `resultados_fase2`:

- `extraccion_documental.html`: explorador con filtros y enlaces a la página del PDF.
- `registros_estructurados.csv`: una fila por registro consolidado.
- `datos_extraidos.csv`: entidades candidatas con fragmento de evidencia.
- `revision_fase2.csv`: registros incompletos o de confianza media.
- `resumen_registros_por_mes.csv`: conteos e importes candidatos por mes y tipo.
- `resumen_nomina_por_persona.csv`: recibos e importes netos candidatos por persona.
- `resumen_importes_por_tipo.csv`: importes candidatos por tipo de registro.
- `valores_recurrentes.csv`: RFC, CURP, cuentas, CLABE, UUID y folios recurrentes.
- `extraccion_fase2.sqlite3`: base estructurada completa.

Para construir la trazabilidad desde la base de fase 2 más reciente:

```powershell
.\ejecutar_fase3.ps1
```

Los resultados se guardan en `resultados_fase3`:

- `trazabilidad_documental.html`: explorador de operaciones relacionadas y enlaces a su evidencia.
- `trazabilidad_operaciones.csv`: una fila por operación o caso consolidado.
- `vinculos_trazabilidad.csv`: relaciones aceptadas y sugeridas con puntuación y reglas.
- `vinculos_sugeridos.csv`: alternativas ambiguas para revisión.
- `pendientes_trazabilidad.csv`: pagos para los que aún falta una contraparte documental.
- `cobertura_trazabilidad.csv`: cobertura de relación por tipo de registro.
- `resumen_trazabilidad_por_mes.csv`: control mensual sin duplicar etapas del mismo pago.
- `trazabilidad.sqlite3`: base autocontenida de casos, vínculos y registros de origen.

## Límites

La clasificación y las fechas dependen del texto OCR. Un resultado con confianza alta significa que varias reglas coincidieron; no certifica el contenido ni una operación contable. Los CSV conservan rutas y advertencias para revisar el PDF. La base y los resultados contienen información del expediente y están excluidos de Git.

Una suma candidata no equivale a un total contable. La fase 3 evita sumar varias veces las etapas del mismo pago, pero mantiene separados el detalle individual de nómina y los lotes que pueden agrupar muchos recibos.
