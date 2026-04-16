import json
import logging
from datetime import datetime
from pathlib import Path


class JsonlEventLogger:
    def __init__(self, file_path: Path):
        self.file_path = file_path

    def event(self, event_type: str, **data) -> None:
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event_type,
            **data,
        }
        with self.file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def setup_run_logging(logs_root: Path) -> tuple[logging.Logger, Path, JsonlEventLogger]:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = logs_root / "runs" / f"galenius_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    log_file = run_dir / "galenius_login.log"
    events_file = run_dir / "events.jsonl"

    logger = logging.getLogger(f"galenius_login_{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger, run_dir, JsonlEventLogger(events_file)
