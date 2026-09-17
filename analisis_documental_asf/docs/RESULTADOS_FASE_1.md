# Resultados de la fase 1

Fecha de ejecución: 17 de septiembre de 2026.

El catálogo se construyó desde la base de estado de OCR Masivo y los PDF con OCR. La lectura fue local y no modificó los originales ni los PDF procesados.

## Cobertura

| Indicador | Resultado |
| --- | ---: |
| PDF catalogados | 538 |
| Páginas indexadas | 13,729 |
| PDF con OCR completado | 538 |
| PDF con texto extraíble | 538 |
| Advertencias de lectura | 0 |
| Clasificaciones pendientes | 0 |

## Tipo principal

| Tipo | Documentos |
| --- | ---: |
| Cheque | 269 |
| Transferencia | 119 |
| Nómina | 63 |
| Póliza | 48 |
| Balanza de comprobación | 10 |
| Estado analítico del presupuesto | 10 |
| Finiquito | 9 |
| Expediente de pago especial | 7 |
| Estado de cuenta | 2 |
| Cuenta pública | 1 |

El tipo principal describe el expediente. Cada registro conserva además todos los tipos detectados en su interior, la evidencia de las reglas, fechas, términos frecuentes, cobertura de texto y enlaces locales al original y al PDF con OCR.

## Entregables locales

La carpeta `resultados` contiene el informe HTML, los CSV por documento, mes y tipo, y el índice SQLite de texto por página. Esa carpeta está excluida de Git porque contiene rutas, texto y datos de los expedientes.

La clasificación usa el nombre y señales del contenido. Es un inventario operativo y no sustituye la validación documental o contable. La fase 2 ya utiliza el índice FTS5 para extraer importes, folios, cuentas, beneficiarios y referencias sin repetir el OCR; consulte sus [resultados](RESULTADOS_FASE_2.md).
