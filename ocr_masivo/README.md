# OCR Masivo Local

Aplicación de consola para Windows y Python 3.11 o superior. Recorre subcarpetas, crea copias PDF buscables y extrae el texto completo de todas las páginas con PyMuPDF. SQLite guarda el estado por ruta absoluta y SHA-256 del original. El OCR se ejecuta localmente mediante OCRmyPDF y Tesseract: la aplicación no incluye conexiones de red ni carga documentos a servicios externos.

## Uso sencillo: solo PDF con OCR (v1.1)

La configuración predeterminada procesa un documento a la vez y no genera TXT. Conserva los registros mínimos de reanudación y errores. Para comenzar:

Durante el inventario se muestra el archivo que se está leyendo. Durante OCR, los contadores, barra y tiempos aparecen separados del nombre del archivo. El tiempo restante necesita documentos terminados y es aproximado: no conoce cuánto tardará cada PDF. La última página observada en el log indica actividad, no una página finalizada; la barra avanza al terminar y validar documentos completos. Las mejoras de pantalla se aplican al iniciar una nueva ejecución, sin modificar procesos ya abiertos.

```powershell
.\ejecutar_ocr.ps1 -Entrada 'D:\MisPDF' -Salida 'D:\MisPDF_con_OCR'
```

Ahora se integra la orientación robusta en el OCR normal mediante una [extensión de OCRmyPDF](https://ocrmypdf.readthedocs.io/en/latest/plugins.html). Primero utiliza OSD; si su confianza es baja, compara cuatro giros en una vista previa limitada a 1800 píxeles por lado. Cada lectura tiene hasta 15 segundos y las cuatro comparten un presupuesto máximo de 60 segundos adicionales por página. Si hay empate, poca evidencia o timeout, conserva la orientación y registra una advertencia para revisión. Las páginas con texto se omiten en modo `skip`.

Para escaneos pobres se solicita renderizado a un mínimo de 300 DPI (no recupera detalles ausentes del original). Solo en la imagen enviada al OCR se aplica gris y contraste suave 1.10 cuando su dispersión tonal es baja. No se borran bordes, líneas de tablas ni caracteres. Esta heurística no garantiza una mejora de exactitud; puede desactivarse con `--no-mejorar-escaneo`. Para utilizar únicamente la rotación estándar: `--no-orientacion-robusta`.

El modo normal no ejecuta la calibración ni extrae campos contables. Las copias originales y los resultados históricos permanecen conservados. Los resultados completados que se reutilicen no se vuelven a reconocer automáticamente al cambiar opciones: para comparar mejoras utilice carpetas de salida y logs nuevas.

Los TOML existentes conservan sus opciones explícitas: ajuste `workers = 1` y `generar_txt = false` si aún tienen los valores anteriores. Para pedir TXT expresamente use `--generar-txt`. La puntuación de 76,67 % corresponde a la calibración anterior; no es una medición de esta nueva combinación adaptativa.

## Instalación en Windows

Abra PowerShell dentro de `ocr_masivo`. Se recomienda Python de 64 bits. La instalación de paquetes requiere internet, pero el procesamiento no.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Si PowerShell impide activar el entorno, puede usar directamente `.\.venv\Scripts\python.exe` en lugar de `python` en todos los comandos, sin cambiar políticas del equipo. `requirements.txt` instala también pytest y Ruff. Para instalar solo la aplicación: `python -m pip install .`.

Instale explícitamente Tesseract de 64 bits:

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
```

En el instalador seleccione los datos de español. Si faltan, descargue `spa.traineddata` desde [tessdata oficial](https://github.com/tesseract-ocr/tessdata_fast/blob/main/spa.traineddata) y cópielo a la carpeta `tessdata` de Tesseract (habitualmente `C:\Program Files\Tesseract-OCR\tessdata`; puede requerir permisos de administrador). Para `spa+eng`, compruebe también `eng.traineddata`. La rotación requiere los datos `osd.traineddata`, incluidos habitualmente con Tesseract.

Añada la carpeta de Tesseract al PATH de Windows y abra una consola nueva. Verifique:

```powershell
tesseract --version
tesseract --list-langs
python -m ocrmypdf --version
```

OCRmyPDF puede encontrar Tesseract en ubicaciones estándar. Si usa una instalación personalizada, configúrela en PATH. Para rasterización se comprueba PDFium (`pypdfium2`, instalado como dependencia de OCRmyPDF) o Ghostscript. Instale Ghostscript de 64 bits desde [su página oficial](https://ghostscript.com/releases/gsdnld.html), especialmente para PDF/A; no se instala silenciosamente. En instalaciones personalizadas agregue su carpeta `bin` al PATH. Instrucciones de referencia: [instalación oficial de OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/installation.html).

## Diagnóstico y primera prueba

```powershell
python -m ocr_masivo diagnostico
python -m ocr_masivo diagnostico --entrada 'D:\PruebaPDF' --salida 'D:\PruebaOCR'
python -m ocr_masivo inventariar --entrada 'D:\PruebaPDF'
python -m ocr_masivo procesar --entrada 'D:\PruebaPDF' --salida 'D:\PruebaOCR' --workers 1
```

Prepare manualmente una carpeta pequeña con copias de dos o tres PDF no firmados y revise visualmente los resultados. El diagnóstico consulta versiones, idiomas, rasterizador, carpetas, lectura del directorio, escritura y espacio libre. No ejecuta OCR. La lectura de cada documento se comprueba durante inventario/procesamiento. Devuelve código 1 si faltan requisitos.

## Procesamiento masivo

```powershell
python -m ocr_masivo procesar --entrada 'D:\PDF_Originales' --salida 'D:\PDF_OCR' --workers 1
```

Empiece con dos documentos simultáneos. Cada proceso OCRmyPDF usa `--jobs 1` y `OMP_THREAD_LIMIT=1`. El planificador mantiene como máximo `workers` trabajos activos, sin crear miles de futuros. El inventario y las filas del lote se conservan en memoria; los PDF y TXT se leen por bloques o páginas.

También puede ejecutar:

```powershell
.\ejecutar_ocr.ps1 -Entrada 'D:\PDF_Originales' -Salida 'D:\PDF_OCR' -Workers 1
```

El script solicita las rutas si no se proporcionan. Las pasa como argumentos, sin construir comandos a partir de su contenido.

## Configuración

Copie `config.example.toml` a `config.toml` y ajuste `[ocr]`. Las rutas relativas del TOML se resuelven respecto a ese archivo; las rutas por consola respecto al directorio actual. Los argumentos explícitos prevalecen sobre TOML. Sin TOML, las carpetas predeterminadas están en el directorio de ejecución.

```powershell
python -m ocr_masivo procesar --config .\config.toml
python -m ocr_masivo procesar --config .\config.toml --idioma spa+eng --workers 1
```

Las cinco carpetas de entrada, PDF, TXT, errores y logs deben ser distintas y no estar anidadas entre sí. Esto evita descubrir resultados como entradas y sobrescribir evidencia. No se recorren enlaces simbólicos ni uniones de directorios de entrada. Los nombres y subcarpetas se conservan en las salidas.

| Opción | Predeterminado | Efecto |
|---|---|---|
| `--idioma` | `spa` | También admite `spa+eng`, con ambos idiomas instalados |
| `--modo` | `skip` | `skip`, `redo` o `force` |
| `--no-rotacion` | Rotación activada | Desactiva `--rotate-pages` |
| `--no-inclinacion` | Inclinación activada | Desactiva `--deskew` |
| `--tipo-salida` | `pdf` | También `pdfa` |
| `--optimizacion` | `1` | Entre 0 y 3; niveles altos pueden necesitar herramientas adicionales |
| `--timeout-pagina` | `180` | Límite de Tesseract por página en segundos |
| `--workers` | `2` | Documentos simultáneos |
| `--megapixeles` | Sin límite explícito | Usa `--skip-big`: omite OCR de páginas mayores, no las reduce |
| `--no-generar-txt` | TXT activado | Desactiva extracción a archivo TXT |
| `--reintentar-fallidos` | Desactivado | Vuelve a intentar estados de error |
| `--texto`, `--errores`, `--logs` | Carpetas locales correspondientes | Cambian ubicación de artefactos y registro |

`skip` conserva las páginas que ya tienen texto y aplica OCR a las demás. `redo` sustituye OCR previo cuando es posible; requiere `--no-inclinacion` por incompatibilidad de OCRmyPDF. `force` rasteriza páginas y rehace el OCR, por lo que puede perder contenido vectorial; solo se usa si lo solicita explícitamente. Nunca se activa como recuperación automática. Opciones de referencia: [funciones avanzadas de OCRmyPDF](https://ocrmypdf.readthedocs.io/en/stable/advanced.html).

Cambiar opciones no invalida automáticamente los documentos completados: para generar una variante use otras carpetas de PDF, TXT y logs. Nunca se usan `--invalidate-digital-signatures`, `--clean-final` ni `--remove-background`.

## Detener y reanudar

Pulse Ctrl+C una vez: dejan de programarse documentos nuevos y se espera a que terminen los activos. El límite por página afecta Tesseract, no al tiempo total de rasterización/optimización. La espera de detención puede ser larga en documentos grandes. Después se genera el reporte.

Si se cierra la consola o reinicia Windows, vuelva a ejecutar el mismo comando con las mismas carpetas. Los estados `processing` vuelven a pendientes. La aplicación limpia únicamente temporales cuyo nombre y ubicación quedaron registrados como propios. Un cierre forzado podría dejar un temporal vacío creado antes de registrarlo; no se hace limpieza indiscriminada por extensión.

Los completados se omiten solo después de comprobar existencia, hash, apertura, número de páginas, extracción de texto y hash del TXT cuando esté habilitado. Si falta un resultado o se corrompe, se vuelve a generar. Si cambia el original se actualiza su hash y se procesa nuevamente. Los resultados registrados son copias derivadas reemplazables; un archivo preexistente no registrado provoca error en vez de sobrescribirse.

No inicie otro lote mientras un proceso anterior siga activo. Hay bloqueos del sistema operativo en las carpetas de salida, texto y logs; se liberan al terminar el proceso. Use almacenamiento local y una sola instancia por conjunto de carpetas. Los bloqueos no protegen contra programas ajenos que cambien archivos mientras se procesan.

```powershell
python -m ocr_masivo reintentar --solo-fallidos
python -m ocr_masivo reporte --formato csv
# Si el lote usó una carpeta de logs personalizada:
python -m ocr_masivo reintentar --solo-fallidos --logs 'D:\MiLoteLogs'
python -m ocr_masivo reporte --formato csv --logs 'D:\MiLoteLogs'
```

Reintentar/reporte recuperan las rutas del último lote guardado en esa base de datos. `reintentar` selecciona únicamente errores del inventario anterior; los firmados/cifrados se vuelven a validar y continúan excluidos si mantienen esa condición. Cada base almacena el estado más reciente por ruta, no un historial versionado de todos los intentos. Los logs técnicos por documento corresponden al último intento.

## Resultados y errores

- `salida_pdf/`: PDF derivados, con las mismas subcarpetas y nombres originales.
- `texto/`: TXT UTF-8, con separadores de página. Se extraen **todas** las páginas desde el PDF final, incluso aquellas que ya tenían texto digital.
- `errores/`: un manifiesto `.pdf.error.json` por documento problemático; contiene ruta original, estado, mensaje y excepción técnica. No se mueven ni duplican los PDF originales.
- `logs/estado.sqlite3`: estado persistente, metadatos, SHA-256 de originales, PDF y TXT.
- `logs/ocr_masivo.log`: log general; archivos por identificador `.stdout.log` y `.stderr.log` separan las salidas de OCRmyPDF.
- `logs/reporte.csv`: CSV UTF-8 con BOM para Excel, una fila por documento del último inventario/lote.
- `logs/resumen.json`: totales, páginas, estados, duración y tamaños. El tiempo de lote mide el procesamiento tras inventariar; el tiempo acumulado suma los últimos intentos por documento y puede superar el tiempo de pared por el paralelismo.

Los errores individuales no detienen los demás documentos. Un fallo global de disco, SQLite o permisos puede impedir continuar o producir el reporte; conserve la base y repita el comando tras resolverlo. Los CSV y resúmenes se reemplazan de manera atómica. El código de salida del procesamiento es 2 si hay documentos con errores, 1 para un problema global y 0 cuando termina sin errores registrados.

La detección de firmas busca objetos de firma, campos con valor y ByteRange; es conservadora y no certifica criptográficamente la firma. También reconoce rechazos de OCRmyPDF. Los documentos firmados quedan en `signed`, los cifrados (incluso con contraseña de usuario vacía) en `encrypted`, y los dañados o con validación fallida en `validation_failed`. No se quitan contraseñas ni firmas.

Cada PDF se escribe primero en un temporal `.partial.pdf` junto al destino. Se valida tamaño, apertura sin reparación, páginas y extracción, y se publica con `os.replace`. El TXT tiene su propio temporal y renombrado atómico; PDF y TXT **no constituyen una única transacción del sistema de archivos**. SQLite permite recuperar una interrupción entre ambos. Las páginas sin texto reconocible pueden ser legítimamente blancas: se permite resultado vacío y se registra una advertencia cuando todo el documento carece de texto. El éxito estructural no garantiza OCR completo ni correcto; revise advertencias de Tesseract y los casos limitados por tiempo/megapíxeles.

El OCR puede equivocarse en montos, fechas, folios y fundamentos jurídicos. Verifique visualmente datos relevantes. Conserve siempre los originales como evidencia: los PDF OCR son copias derivadas.

## Desarrollo y pruebas

```powershell
python -m pytest -q
python -m ruff check src tests
python -m compileall -q src
```

Las pruebas generan PDF sintéticos dentro de carpetas temporales y simulan OCRmyPDF. No procesan documentos reales ni requieren Tesseract. El protocolo `Engine` de `processor.py` permite añadir posteriormente un adaptador EasyOCR conservando validación, publicación y registro; no existe un fallback automático.

No incluye interfaz gráfica, certificación de firmas, descifrado ni revisión visual automatizada. La instalación y diagnóstico de motores externos deben completarse en el equipo antes del primer lote real.

## Calibración controlada v2 completada

El [resumen de avances](docs/AVANCES.md) contiene los resultados publicables. El informe detallado permanece local en `prueba_resultados/auditoria_precision_v2/informe_precision_v2.html`, junto con las evidencias privadas excluidas de GitHub. Se conservaron las mismas 12 páginas, 60 campos y referencias de la auditoría anterior. El perfil A reproduce 39/60; B obtiene 46/60, C 42/60 y D 43/60. B alcanza 19/23 importes y 8/10 campos del estado analítico. Ningún perfil alcanza todos los umbrales; estos resultados no se generalizan al lote completo.

Los módulos de `src/ocr_masivo/calibration/` implementan orientación, ensayos, validadores, comparación y publicación. Son herramientas de calibración con regiones de evaluación conocidas; no cambian el motor predeterminado ni ejecutan el lote completo. Los errores y alternativas quedan conservados junto con el texto crudo y los recortes originales. `preservacion.json` registra hashes de originales, PDF anteriores y auditoría inicial.

En este equipo se instaló el extra `calibracion` (OpenCV) y el modelo español oficial [tessdata_best](https://github.com/tesseract-ocr/tessdata_best), guardado en `herramientas/tessdata_best/spa.traineddata`. Las lecturas almacenan hashes de modelo e imagen. Tesseract permanece como dependencia externa. El perfil C utiliza best a 300 DPI; D, best a 400 DPI con PSM 3, 4, 6 y 11 y regiones. Los ensayos de binarización también se conservan.

Los siguientes comandos requieren los documentos y la auditoría congelada de la instalación original, además de los modelos externos. Estos datos no se distribuyen en el repositorio. Desde esa instalación, para reproducir exclusivamente la muestra congelada (reutiliza resultados almacenados):

```powershell
.venv\Scripts\python.exe -m pip install -e '.[calibracion]'
.venv\Scripts\python.exe -m ocr_masivo.calibration.runner --stage all
.venv\Scripts\python.exe -m ocr_masivo.calibration.fields
.venv\Scripts\python.exe -m ocr_masivo.calibration.report
```

Para reconstruir únicamente HTML/CSV/JSON, use solo la última orden. No borre la evidencia ni la línea base para repetir una calibración. Los tiempos acumulados incluyen alternativas; los tiempos de pared por etapa pueden reflejar reanudaciones y no deben sumarse como si fueran una ejecución continua.

Se recomienda B para continuar pruebas de PDF buscable; la extracción de tablas y cifras necesita revisión humana. El siguiente experimento mínimo consiste en segmentar celdas y ajustar márgenes sobre B, contrastar una segunda lectura numérica y evaluar nuevamente los mismos 60 campos, sin seleccionar resultados por su coincidencia con la referencia.
