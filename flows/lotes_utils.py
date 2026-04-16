import shutil
from pathlib import Path


def prune_old_lote_dirs_global(lotes_root: Path, max_lote_dirs: int) -> None:
    if max_lote_dirs <= 0 or not lotes_root.exists():
        return

    lote_dirs = [path for path in lotes_root.iterdir() if path.is_dir()]
    if len(lote_dirs) <= max_lote_dirs:
        return

    lote_dirs.sort(key=lambda path: path.stat().st_mtime)
    for path in lote_dirs[:-max_lote_dirs]:
        shutil.rmtree(path, ignore_errors=True)
