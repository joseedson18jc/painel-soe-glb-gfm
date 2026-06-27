"""Pacote de automacao ETL do painel S&OE GLB-GFM.

Nucleo 100% stdlib (Python 3.9+). Dependencias opcionais (numpy, sqlalchemy,
pyodbc, requests, msal) sao importadas de forma LAZY apenas quando a source ou
feature correspondente e exigida.
"""

__all__ = [
    "config",
    "sources",
    "compute",
    "montecarlo",
    "validate",
    "notify",
    "publish",
    "state",
]

__version__ = "1.0.0"
