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
