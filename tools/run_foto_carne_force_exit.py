import logging
import os
import sys
import traceback
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def main() -> int:
    try:
        import run_foto_carne

        return int(run_foto_carne.main())
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 1
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        logging.shutdown()


if __name__ == "__main__":
    os._exit(main())
