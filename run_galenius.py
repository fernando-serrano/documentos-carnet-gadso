from galenius_flow.config import load_galenius_config
from galenius_flow.logging_utils import setup_run_logging
from galenius_flow.main_flow import GaleniusFlowError, ejecutar_flujo_galenius


def main() -> int:
    cfg = load_galenius_config()
    logger, run_dir, event_logger = setup_run_logging(cfg.logs_root, run_name="galenius_flow")

    logger.info("[GALENIUS] Run dir: %s", run_dir)
    event_logger.event("run_start", run_dir=str(run_dir))

    try:
        resumen = ejecutar_flujo_galenius(cfg, logger, event_logger)
        logger.info(
            "[GALENIUS] Flujo completado | descargados=%s | comprimidos=%s | err_descarga=%s | err_compresion=%s",
            resumen.get("descargados", 0),
            resumen.get("comprimidos", 0),
            resumen.get("errores_descarga", 0),
            resumen.get("errores_compresion", 0),
        )
        event_logger.event("run_finish", status="ok", **resumen)
        return 0
    except GaleniusFlowError as exc:
        logger.error("[GALENIUS] Flujo fallido: %s", exc)
        event_logger.event("run_finish", status="error", detail=str(exc))
        return 2
    except Exception as exc:
        logger.exception("[GALENIUS] Error inesperado")
        event_logger.event("run_finish", status="error", detail=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
