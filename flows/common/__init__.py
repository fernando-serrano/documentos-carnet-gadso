"""Paquete de utilidades compartidas entre los subflujos documentales.

Centraliza la lógica que antes estaba duplicada en cada ``flows/*_flow``
(integración con Google Sheets, helpers de texto, logging y config).
Cada subflujo expone wrappers delgados que fijan sus parámetros propios
(User-Agent, prefijos de variables de entorno, cadena de credenciales).
"""
