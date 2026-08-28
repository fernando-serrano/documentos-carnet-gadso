"""CLI: decide que lane (A/B) de la cola compartida BOT DOCUMENTOS usar en esta corrida.

Imprime 'A' o 'B' a stdout y sale con codigo 0. Si ambas lanes estan
ocupadas (o la lane B no esta configurada), no imprime nada util a stdout,
intenta avisar por correo (Microsoft Graph, mismo canal que enviar_lote.py)
y sale con codigo 3. No escribe en ninguna hoja.

Uso (desde run.bat):
    for /f "delims=" %%W in ('".venv\\Scripts\\python.exe" scripts\\select_workbook.py') do set "ACTIVE_WORKBOOK=%%W"
"""
from __future__ import annotations

import os
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from flows.common.workbook_lock import decidir_lane
from flows.notifications.graph_client import send_mail
from flows.notifications.mail_config import graph_mail_enabled, load_mail_config, validate_mail_config


def _avisar_ambas_lanes_ocupadas() -> None:
    if not graph_mail_enabled():
        return
    config = load_mail_config()
    if validate_mail_config(config):
        return

    dispositivo = str(os.getenv("COMPUTERNAME", "") or socket.gethostname() or "desconocido").strip()
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    subject = f"{config['subject_prefix']} - Ambas lanes de cola ocupadas ({dispositivo})"
    body = (
        f"<p>El dispositivo <b>{dispositivo}</b> intento iniciar run.bat el <b>{ahora}</b> "
        "pero tanto la lane A como la lane B de la cola BOT DOCUMENTOS ya tienen registros "
        "EN PROCESO.</p>"
        "<p>La corrida se aborto sin tocar ninguna hoja. Reintentar mas tarde o revisar "
        "manualmente el estado de ambas colas.</p>"
    )
    try:
        send_mail(config, subject, body)
    except Exception as exc:
        print(f"[SELECT_WORKBOOK] No se pudo enviar el correo de aviso: {exc}", file=sys.stderr)


def main() -> int:
    load_dotenv()
    lane = decidir_lane()
    if lane is None:
        _avisar_ambas_lanes_ocupadas()
        print("[SELECT_WORKBOOK] Ambas lanes ocupadas o lane B no configurada.", file=sys.stderr)
        return 3

    print(lane)
    return 0


if __name__ == "__main__":
    sys.exit(main())
