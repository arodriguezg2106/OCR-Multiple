# Avances de OCR Masivo Local

## Actualización 1.1 — 14 de septiembre de 2026

La orientación adaptativa ya está integrada en el procesamiento normal. OSD concluyente evita lecturas extra; cuando hay dudas se puntúan cuatro giros de una vista previa y se rechazan decisiones ambiguas. Se incorpora contraste conservador solo para OCR y renderizado mínimo a 300 DPI. Por defecto se genera PDF sin TXT y se utiliza un solo trabajador. Las referencias y la configuración A/B de la calibración anterior se conservan explícitamente; sus métricas no evalúan esta nueva combinación.

Verificación de la integración: 55 pruebas automatizadas y una prueba real limitada a cuatro PDF de una página (dos escaneos sintéticos girados, una página digital y una página del estado analítico). Los cuatro terminaron, con texto horizontal, sin generar TXT y con el hash del documento original conservado. Esta prueba comprueba funcionamiento y orientación, no una nueva exactitud de los 60 campos.

## Funcionalidad implementada

- OCR local con OCRmyPDF y Tesseract, PDF buscables y extracción de texto.
- Inventario, hashes SHA-256, estado en SQLite y reanudación de trabajos.
- Validación de resultados y conservación de originales.
- Explorador HTML de trazabilidad por documento y página.
- Calibración de orientación, segmentación y reconocimiento; validadores de importes, fechas, cuentas y folios.
- Comparación de lecturas con resultados crudos y señalamiento de discrepancias.

## Calibración v2

Muestra dirigida fija de 12 páginas y 60 campos. Se conservaron los valores esperados y la auditoría anterior. Resultados de la ejecución local:

| Perfil | Exactos | Importes | Fechas | Cuentas | Folios | Estado analítico |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A: configuración original | 39/60 (65 %) | 12/23 | 11/13 | 6/10 | 10/14 | 0/10 |
| B: orientación robusta | 46/60 (76,67 %) | 19/23 | 12/13 | 6/10 | 9/14 | 8/10 |
| C: tessdata_best español, 300 DPI | 42/60 (70 %) | 16/23 | 10/13 | 6/10 | 10/14 | 7/10 |
| D: 400 DPI, páginas y regiones, PSM 3/4/6/11 | 43/60 (71,67 %) | 17/23 | 9/13 | 7/10 | 10/14 | 8/10 |

B/C/D orientaron correctamente las 12 páginas. B fue el mejor según la prioridad de importes, cuentas, folios, fechas y tiempo. No se alcanzaron conjuntamente los umbrales de 85 % total, 80 % de importes y 9/10 del estado analítico. Persisten errores numéricos, omisiones y ambigüedades alfanuméricas.

Estos resultados no son generalizables. No se ejecutó el lote completo ni se habilitó extracción contable automática. La segmentación de evaluación utiliza regiones visualmente anotadas; no constituye un extractor universal de tablas.

## Verificación y alcance del repositorio

Al cierre de la calibración se aprobaron 45 pruebas automatizadas, Ruff, compilación y comprobación de dependencias. Las pruebas usan datos sintéticos y no requieren los documentos privados ni ejecutar OCR real.

El informe navegable, los CSV, las referencias visuales y las evidencias permanecen en la instalación original, dentro de `prueba_resultados/auditoria_precision_v2`. No forman parte de GitHub. Los comandos de calibración requieren esos archivos locales, los PDF de prueba y los modelos de Tesseract; una clonación por sí sola no permite reproducir la auditoría privada.

## Próximo paso

Evaluar segmentación por celdas sobre B y una segunda lectura numérica independiente, conservando la misma muestra y las discrepancias. Posteriormente validar con otra muestra independiente antes de ampliar el procesamiento.
