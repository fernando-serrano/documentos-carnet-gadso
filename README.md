# Flujo Galenius - Tratamiento Documental

Este repositorio, en este contexto de trabajo, se centra exclusivamente en el flujo de tratamiento documental desde Galenius.

No incluye ni montara en este script el otro flujo de acceso/operacion en SUCAMEC.

## Proposito

Automatizar el tratamiento de documentos medicos para reducir carga operativa manual y asegurar trazabilidad por archivo y por ejecucion.

## Alcance Funcional

La arquitectura contempla 3 tratamientos documentales:

1. Descarga documental por lote desde Galenius.
2. Guardado local con nomenclatura estandarizada y control de unicidad.
3. Post-tratamiento documental (validaciones, normalizacion y consumo por procesos posteriores).

## Estado Actual

Actualmente se esta afinando el tratamiento 1:

- Descarga por lote.
- Guardado local de documentos.
- Trazabilidad de ejecucion por logs.

## Entrada Operativa

La hoja que inicializa el flujo es la pestaña `BOT DOCUMENTOS` del spreadsheet de Galenius.

Campos usados en esta etapa:

- `DNI`
- `ESTADO CERTIFICADO MEDICO`
- `RESPONSABLE`
- `FECHA TRAMITE`

El responsable se establece por defecto como `BOT DOCUMENTOS SUCAMEC` y la fecha de tramite se actualiza con la fecha y hora de la interacion.

## Salida Local

Cada ejecucion crea su propio directorio de lote en la raiz del proyecto:

- `lotes/lote-DD-MM-YYYY-HH-MM-SS/<dni>/certificado_medico_<dni>.pdf`

Si el PDF supera el umbral configurado, el flujo intenta optimizarlo antes de guardarlo.

## Estructura Relevante

- `run_galenius.py`: entrypoint unico del flujo.
- `run_galenius_login.bat`: launcher de Windows para ejecutar el flujo unico.
- `galenius_flow/config.py`: configuracion por variables de entorno.
- `galenius_flow/main_flow.py`: orquestacion del flujo actual.
- `galenius_flow/selectors.py`: selectores de UI y candidatos de scraping.
- `galenius_flow/scraping_utils.py`: utilidades de barrido y deteccion robusta.
- `galenius_flow/logging_utils.py`: logging por corrida y eventos JSONL.
- `ejemplos/.env.galenius.example`: plantilla de variables para este flujo.

## Variables Minimas

Configurar en `.env`:

- `GALENIUS_URL_LOGIN`
- `GALENIUS_USERNAME`
- `GALENIUS_PASSWORD`

Opcionales recomendadas:

- `GALENIUS_OUTPUT_DIR`
- `GALENIUS_MAX_PDF_KB`
- `GALENIUS_BATCH_SIZE`
- `GALENIUS_OVERWRITE_EXISTING`
- `GALENIUS_LOG_DIR`

## Retencion De Auditoria

La carpeta de auditoria conserva como maximo 10 corridas historicas por defecto. Ese limite puede ajustarse con `GALENIUS_AUDIT_MAX_RUN_DIRS`.

## Retencion De Lotes

La carpeta `lotes` conserva como maximo 10 lotes por defecto. Ese limite puede ajustarse con `GALENIUS_MAX_LOTE_DIRS`.

## Validacion De Peso

El peso maximo del PDF usa un margen de seguridad por debajo del umbral configurado en `GALENIUS_MAX_PDF_KB`, para evitar quedar al limite exacto del valor permitido.

## Ejecucion

```bat
run_galenius_login.bat
```

Por defecto el navegador corre en modo invisible (`GALENIUS_HEADLESS=1`) para evitar ventanas abiertas durante el procesamiento. Si necesitas depurar visualmente, puedes cambiarlo a `0` de forma temporal.

## Logs

Por cada corrida se generan archivos en:

- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/galenius_flow.log`
- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/events.jsonl`

## Nota De Contexto

Este README aplica solo al flujo documental Galenius de este script.
Cualquier logica del otro flujo (SUCAMEC) se gestiona en otro script/chat y no forma parte de este alcance.
