"""CLI: decide que lane (A/B) de la cola compartida BOT DOCUMENTOS usar en esta corrida.

Regla binaria: si la lane A tiene CUALQUIER fila EN PROCESO, se usa B sin mas
condiciones (sin importar si a A le queda trabajo pendiente real o si B
tiene DNIs cargados); si A no esta en proceso, se usa A. Imprime 'A' o 'B' a
stdout y sale con codigo 0. Si A esta en proceso y GALENIUS_QUEUE_SHEET_URL_B
no esta configurada, no imprime nada util y sale con codigo 3. No escribe en
ninguna hoja.

Uso (desde run.bat):
    for /f "delims=" %%W in ('".venv\\Scripts\\python.exe" scripts\\select_workbook.py') do set "ACTIVE_WORKBOOK=%%W"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from flows.common.workbook_lock import decidir_lane


def main() -> int:
    load_dotenv()
    lane = decidir_lane()
    if lane is None:
        print("[SELECT_WORKBOOK] Lane A esta en proceso y GALENIUS_QUEUE_SHEET_URL_B no esta configurada.", file=sys.stderr)
        return 3

    print(lane)
    return 0


if __name__ == "__main__":
    sys.exit(main())
