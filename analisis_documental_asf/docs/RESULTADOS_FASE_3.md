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
