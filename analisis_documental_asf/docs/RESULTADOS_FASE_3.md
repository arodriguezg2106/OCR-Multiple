# Resultados de la fase 3

Fecha de ejecución: 17 de septiembre de 2026.

La trazabilidad se construyó localmente a partir de los 9,558 registros estructurados de la fase 2. No volvió a ejecutar OCR y no modificó los PDF. Cada vínculo conserva los registros, documentos y páginas que lo sustentan.

## Cobertura

| Indicador | Resultado |
| --- | ---: |
| Registros de origen | 9,558 |
| Casos consolidados | 8,726 |
| Casos de pago | 1,339 |
| Casos de nómina individual | 7,387 |
| Casos con dos o más registros relacionados | 536 |
| Trazabilidades documentales | 512 |
| Casos conciliados con movimiento bancario | 24 |
| Vínculos aceptados automáticamente | 832 |
| Vínculos sugeridos para revisión | 195 |
| Casos de pago todavía sin conciliar | 815 |

Los 24 casos conciliados con banco se dividen en 12 pagos respaldados por transferencia y 12 recibos de nómina. La cobertura bancaria está limitada por el material disponible: el inventario contiene 263 movimientos procedentes de sólo dos estados de cuenta.

## Vínculos aceptados

| Relación | Vínculos |
| --- | ---: |
| Póliza u orden → cheque | 714 |
| Cheque → lote de transferencia | 93 |
| Póliza → lote de transferencia | 1 |
| Registro documental → movimiento bancario | 24 |

La conciliación automática exige evidencia fuerte. Usa números de cheque encontrados dentro de la póliza, folios completos, importes exactos, fechas normalizadas, beneficiarios semejantes y cercanía de páginas. Cuando un importe común produce varias alternativas —por ejemplo `$4,251.00`— el sistema no fuerza la relación y la guarda en `vinculos_sugeridos.csv`.

## Cobertura por tipo de registro

| Tipo | Total | Vinculados | Sin vincular |
| --- | ---: | ---: | ---: |
| Recibo de nómina | 7,387 | 12 | 7,375 |
| Póliza u orden de pago | 1,113 | 713 | 400 |
| Cheque | 638 | 515 | 123 |
| Movimiento bancario | 263 | 24 | 239 |
| Lote de transferencia | 157 | 104 | 53 |

## Entregables

- `trazabilidad_documental.html`: explorador de casos con búsqueda, filtros y enlaces a cada página del PDF.
- `trazabilidad_operaciones.csv`: una fila por caso consolidado, con etapas, faltantes, documentos y claves de origen.
- `vinculos_trazabilidad.csv`: detalle auditable de cada relación y de las reglas que la sustentan.
- `vinculos_sugeridos.csv`: coincidencias ambiguas que requieren validación humana.
- `pendientes_trazabilidad.csv`: casos de pago que todavía tienen un solo registro.
- `cobertura_trazabilidad.csv`: cobertura por tipo documental.
- `resumen_trazabilidad_por_mes.csv`: casos, vínculos bancarios e importe candidato por carpeta mensual.
- `trazabilidad.sqlite3`: base autocontenida con casos, vínculos y copia de los registros de fase 2.

## Interpretación

El importe del caso se elige una sola vez con prioridad al movimiento bancario, transferencia, cheque y póliza. Así no se suman como operaciones distintas las representaciones del mismo pago. Los importes diferentes entre etapas se conservan y se señalan en el informe; pueden corresponder a importes brutos, descuentos, retenciones o errores OCR.

Los totales de nómina y los pagos por lote se mantienen como grupos distintos. No debe sumarse un total general entre ambos, porque una transferencia de nómina puede contener muchos recibos individuales. Los vínculos sugeridos y los 815 pagos pendientes son la cola de revisión para ampliar la trazabilidad cuando existan más estados de cuenta o se corrijan datos OCR.

## Cruce con la relación de pagos

Se analizaron las 520 filas de `Relación de pagos.xlsx` contra los 9,558 registros con evidencia de la fase 2. El cruce vinculó automáticamente 501 filas con un PDF y dejó 19 sin un vínculo suficientemente fuerte. La copia de la fuente se identifica mediante SHA-256 y el archivo original no se modificó.

| Control | Importe |
| --- | ---: |
| Total almacenado por Excel | $45,422,056.12 |
| Total esperado informado | $45,436,913.58 |
| Diferencia inicial | $14,857.46 |
| Ajustes con evidencia fuerte | $53,095.98 |
| Total después de esos ajustes | $45,475,152.10 |
| Diferencia pendiente contra el objetivo | -$38,238.52 |

Los ajustes con evidencia fuerte son:

- La fila 349 contiene `93.198.98` como texto. El cheque y el lote de `POLIZA0140.pdf` muestran $93,198.98, pero la suma de Excel excluye esa celda.
- La fila 395 contiene $11,577.00. El cheque y el lote de `POLIZA0193.pdf`, páginas 8 y 9, muestran $111,577.00.
- Las filas 126/127, 409/410, 416/417 y 424/425 repiten cuenta, mes, importe y concepto, y cada par apunta al mismo documento. Conservar una sola fila de cada par resta $140,103.00.

Estas correcciones explican errores concretos, pero no prueban por sí solas el total esperado: después de aplicarlas, la relación queda $38,238.52 por encima. El archivo `hallazgos_relacion_pagos.csv` también separa las diferencias entre el importe de la hoja y el importe pagado en cheque o transferencia. Esas diferencias permanecen como revisión porque algunas pólizas registran importe bruto y el cheque registra importe neto; no se deben aplicar al total sin fijar primero la base contable del objetivo.

La fila 64 aporta una pista para el saldo restante: la hoja clasifica $38,916.16 como `PARTICIP.`, pero el cheque 612 de `Pago de laudo por sentencia.pdf`, página 5, indica `ARBITRIOS 2020`. Si ese pago se excluye del universo esperado, el total corregido queda $677.64 por debajo del objetivo. Esta relación aritmética es una hipótesis de revisión; hace falta confirmar qué fuentes de financiamiento abarca el total esperado antes de aplicarla.
