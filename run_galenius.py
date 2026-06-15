from flows.galenius_flow.config import load_galenius_config
from flows.galenius_flow.logging_utils import setup_run_logging
from flows.galenius_flow.main_flow import GaleniusFlowError, ejecutar_flujo_galenius


def main() -> int:
    cfg = load_galenius_config()
    logger, run_dir, event_logger = setup_run_logging(cfg.logs_root, run_name="galenius_flow", max_run_dirs=cfg.audit_max_run_dirs)

    logger.info("[GALENIUS] Run dir: %s", run_dir)
    event_logger.event("run_start", run_dir=str(run_dir))

    try:
        resumen = ejecutar_flujo_galenius(cfg, run_dir, logger, event_logger)
        logger.info(
            "[GALENIUS] Flujo completado | workers=%s | procesados=%s | descargados=%s | sin_resultados=%s | errores=%s",
            resumen.get("workers", 0),
            resumen.get("procesados", 0),
            resumen.get("descargados", 0),
            resumen.get("sin_resultados", 0),
            resumen.get("errores", 0),
        )
        event_logger.event("run_finish", status="ok", **resumen)

        # Envio del lote por correo (omitido en modo orquestado run.bat all -> LOTE_MAIL_DEFER=1).
        lote_dir = resumen.get("lote_dir")
        if lote_dir:
            try:
                from flows.notifications.enviar_lote import enviar_lote_post_run

                enviar_lote_post_run(lote_dir, "galenius", logger)
            except Exception as exc:
                logger.warning("[GALENIUS] No se pudo enviar el lote por correo: %s", exc)
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
