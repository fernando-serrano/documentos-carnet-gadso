from dataclasses import dataclass
from pathlib import Path


@dataclass
class DocumentoLote:
    record_id: str
    source_name: str
    target_name: str
    local_path: Path | None = None


@dataclass
class ResultadoDocumento:
    record_id: str
    target_name: str
    estado: str
    comprimido: bool
    detalle: str = ""


class GaleniusBatchPipeline:
    """
    Pipeline segmentado para futuras etapas:
    1) autenticacion
    2) descubrimiento de PDFs
    3) descarga por lote
    4) compresion condicional
    5) resumen final
    """

    def __init__(self, cfg, logger, event_logger):
        self.cfg = cfg
        self.logger = logger
        self.event_logger = event_logger

    def run(self) -> list[ResultadoDocumento]:
        # Se implementa en siguientes iteraciones al integrar HTML de lista/descarga.
        self.logger.info("[GALENIUS] Pipeline base inicializado (pendiente etapa de lote)")
        self.event_logger.event("pipeline_initialized", status="pending_batch_download")
        return []
