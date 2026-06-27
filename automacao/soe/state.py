"""Leitura do data.json anterior: versionamento e base para deltas.

Funcoes puras de I/O minimo (le um JSON do disco). Usadas por validate.py para
calcular meta.delta e por soe_etl.py para incrementar meta.version.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("soe.state")


def load_previous(out_path: Path) -> Optional[Dict[str, Any]]:
    """Le o data.json anterior, se existir e for valido. Senao None."""
    p = Path(out_path)
    if not p.exists():
        return None
    try:
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("data.json anterior ilegivel (%s); ignorando.", exc)
        return None


def next_version(previous: Optional[Dict[str, Any]]) -> int:
    """Incrementa meta.version a partir do anterior (1 se nao houver)."""
    if not previous:
        return 1
    try:
        return int(previous.get("meta", {}).get("version", 0)) + 1
    except (TypeError, ValueError):
        return 1
