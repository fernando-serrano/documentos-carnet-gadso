from sigemo_galenius.config import load_galenius_config
from sigemo_galenius.login_flow import LoginGaleniusError, ejecutar_login_galenius
from sigemo_galenius.logging_utils import setup_run_logging


def main() -> int:
    cfg = load_galenius_config()
    logger, run_dir, event_logger = setup_run_logging(cfg.logs_root)

    logger.info("[GALENIUS] Run dir: %s", run_dir)
    event_logger.event("run_start", run_dir=str(run_dir))

    try:
        final_url = ejecutar_login_galenius(cfg, logger, event_logger)
        logger.info("[GALENIUS] Flujo de login completado | final_url=%s", final_url)
        event_logger.event("run_finish", status="ok", final_url=final_url)
        return 0
    except LoginGaleniusError as exc:
        logger.error("[GALENIUS] Login fallido: %s", exc)
        event_logger.event("run_finish", status="error", detail=str(exc))
        return 2
    except Exception as exc:
        logger.exception("[GALENIUS] Error inesperado")
        event_logger.event("run_finish", status="error", detail=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
