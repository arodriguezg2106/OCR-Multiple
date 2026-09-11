# Prueba de trazabilidad local

Se instaló Tesseract 5.4 mediante Winget. El instalador incluye inglés y orientación.
El modelo español se descargó del repositorio oficial `tesseract-ocr/tessdata_fast`.
Como la carpeta de instalación requiere permisos elevados para escribir, los datos
se guardaron en `herramientas/tessdata`, junto con las configuraciones de Tesseract.
Los scripts de esta carpeta establecen `TESSDATA_PREFIX` únicamente en su proceso.

Desde la raíz del proyecto:

```powershell
# Prueba de los documentos de hasta 20 páginas: seis archivos, 56 páginas en esta muestra.
.\.venv\Scripts\python.exe herramientas\prueba_trazabilidad.py --entrada Prueba --destino prueba_resultados --max-paginas 20

# Crear/actualizar el buscador HTML local a partir de los resultados completados.
.\.venv\Scripts\python.exe herramientas\explorador_trazabilidad.py prueba_resultados
```

El explorador no usa servicios, fuentes ni bibliotecas remotas. Cada página muestra
el texto y enlaces al PDF original y al derivado, con `#page=N`. El visor del navegador
debe admitir ese fragmento para saltar automáticamente a la página. Buscar una misma
cifra en varios documentos no demuestra por sí solo que estén relacionados.

Para los comandos normales de la aplicación, habilite los datos en la sesión:

```powershell
$env:TESSDATA_PREFIX = (Resolve-Path .\herramientas\tessdata).Path
.\.venv\Scripts\python.exe -m ocr_masivo diagnostico --entrada Prueba
```

Para procesar posteriormente los ocho documentos completos con el planificador
principal y reutilizar los seis resultados de la prueba:

```powershell
$env:TESSDATA_PREFIX = (Resolve-Path .\herramientas\tessdata).Path
.\.venv\Scripts\python.exe -m ocr_masivo procesar --entrada Prueba --salida prueba_resultados\pdf --texto prueba_resultados\texto --errores prueba_resultados\errores --logs prueba_resultados\logs --workers 2
```

`prueba_trazabilidad.py` usa los mismos módulos de inventario, procesamiento,
validación, SQLite y reporte, con una selección por páginas. Es un ejecutor de
pruebas pequeñas: para miles de archivos use el planificador principal.
`logs/trazabilidad_paginas.csv` registra número de página, hash y conteos de texto.
`logs/evaluacion_ocr.json` registra la cobertura de texto por documento.
Los dos archivos grandes siguen inventariados en SQLite aunque el CSV de la prueba
incluye únicamente los seis seleccionados.

Estas métricas comprueban integridad y presencia de texto; no miden precisión de
importes, fechas, RFC ni alineación de las columnas. Para una trazabilidad contable
validada es necesario contrastar las coincidencias con las imágenes y evidencias.

## Evaluación visual de 60 campos

La evaluación posterior está en `prueba_resultados/auditoria_precision/informe_precision.html`.
Contiene 60 campos de 12 páginas, transcritos visualmente antes de consultar el OCR,
y el contraste por campo con incidencias explícitas. La muestra es dirigida, no aleatoria.
La lectura fue realizada por el asistente y no constituye una certificación independiente.

Para regenerar los informes desde la referencia y revisión conservadas:

```powershell
.\.venv\Scripts\python.exe herramientas\informe_precision.py prueba_resultados\auditoria_precision
```

El script no obtiene la verdad de referencia del OCR: utiliza la transcripción visual
y las observaciones de revisión guardadas en JSON. Comprueba hashes, cobertura de
los 60 campos y existencia textual de los valores revisados, y publica HTML, CSV y JSON.
La medición no modifica ni vuelve a procesar los PDF.
