"""Flujo modular Galenius para tratamiento documental."""

from .config import GaleniusConfig, load_galenius_config
from .main_flow import ejecutar_flujo_galenius

__all__ = [
    "GaleniusConfig",
    "load_galenius_config",
    "ejecutar_flujo_galenius",
]
