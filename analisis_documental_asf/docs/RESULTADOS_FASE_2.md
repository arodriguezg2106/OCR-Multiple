# Resultados de la fase 2

Fecha de ejecución: 17 de septiembre de 2026.

La extracción se ejecutó localmente sobre las 13,729 páginas indexadas en la fase 1. No volvió a realizar OCR ni modificó los PDF.

## Cobertura

| Indicador | Resultado |
| --- | ---: |
| Documentos recorridos | 538 |
| Páginas analizadas | 13,729 |
| Entidades candidatas | 153,552 |
| Registros estructurados consolidados | 9,578 |
| Registros con confianza alta | 8,085 |
| Registros para revisión | 1,493 |
| Operaciones con evidencia en varias páginas | 493 |

## Registros estructurados

| Tipo | Total | Alta | Revisión |
| --- | ---: | ---: | ---: |
| Recibo de nómina | 7,387 | 6,504 | 883 |
| Póliza u orden de pago | 1,113 | 583 | 530 |
| Cheque | 658 | 607 | 51 |
| Movimiento bancario | 263 | 238 | 25 |
| Lote de transferencia | 157 | 153 | 4 |

## Entidades candidatas

| Entidad | Ocurrencias únicas por página |
| --- | ---: |
| Importe | 97,328 |
| RFC | 16,976 |
| Folio o referencia | 16,765 |
| UUID | 7,622 |
| CURP | 6,628 |
| Fecha | 5,526 |
| Cuenta etiquetada | 1,790 |
| CLABE o número de 18 dígitos | 917 |

Los registros se consolidan con identificadores fuertes cuando están disponibles. Por ejemplo, las páginas que repiten el mismo número de cheque dentro de un expediente se guardan como una operación con varias páginas de evidencia.

## Uso y límites

`revision_fase2.csv` concentra campos incompletos o señales afectadas por OCR. La extracción conserva el valor leído y no corrige silenciosamente nombres, CURP, RFC, cuentas o referencias.

Los importes agregados se denominan **sumas candidatas**. No deben sumarse entre tipos: el mismo pago puede aparecer en la póliza, el cheque, la transferencia y el estado de cuenta. La fase 3 debe relacionar esas representaciones mediante importe, fecha, folio, cuenta y beneficiario para construir la trazabilidad y evitar dobles conteos.
