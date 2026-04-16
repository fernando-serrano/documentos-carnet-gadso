import json
import logging
import shutil
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


def _prune_old_run_dirs(runs_root: Path, max_run_dirs: int) -> None:
    if max_run_dirs <= 0 or not runs_root.exists():
        return

    run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
    if len(run_dirs) <= max_run_dirs:
        return

    run_dirs.sort(key=lambda path: path.stat().st_mtime)
    for path in run_dirs[:-max_run_dirs]:
        shutil.rmtree(path, ignore_errors=True)


def setup_run_logging(logs_root: Path, run_name: str = "galenius", max_run_dirs: int = 10) -> tuple[logging.Logger, Path, JsonlEventLogger]:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    runs_root = logs_root / "runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    run_dir = runs_root / f"{run_name}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    _prune_old_run_dirs(runs_root, max_run_dirs)

    log_file = run_dir / f"{run_name}.log"
    events_file = run_dir / "events.jsonl"

    logger = logging.getLogger(f"{run_name}_{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger, run_dir, JsonlEventLogger(events_file)


def setup_worker_logging(run_dir: Path, worker_id: int) -> tuple[logging.Logger, Path]:
    worker_dir = run_dir / "workers" / f"worker_{worker_id}"
    worker_dir.mkdir(parents=True, exist_ok=True)

    logger_name = f"galenius_worker_{worker_id}_{run_dir.name}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    worker_log_file = worker_dir / f"worker_{worker_id}.log"

    file_handler = logging.FileHandler(worker_log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    return logger, worker_dir