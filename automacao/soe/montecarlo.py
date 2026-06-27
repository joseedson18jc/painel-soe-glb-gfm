"""Simulacao Monte Carlo da cobertura/ruptura agregada.

Modelo: a demanda agregada de cada uma das proximas N semanas (horizonte de
cobertura) e Normal(mu=demanda_sem_total, sigma=sigma_total), com
sigma_total = sqrt(sum sigma_sku^2) (somatorio de variancias, assumindo SKUs
independentes). Simulamos o consumo ao longo do horizonte e calculamos a
cobertura final (em dias) e a probabilidade de ruptura (estoque projetado < 0
em algum momento do horizonte).

  * SEED deterministica derivada da data -> reprodutivel para double-check.
  * numpy acelera SE disponivel; nucleo roda em stdlib puro (random+statistics).
  * Cross-check: media MC vs valor analitico (registra issue se divergir).

Saida casa com data.json['monte_carlo'].
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import config
from .compute import demanda_diaria

logger = logging.getLogger("soe.montecarlo")

HORIZONTE_SEMANAS = config.SEMANAS_HORIZONTE  # consumo projetado p/ cobertura
N_BINS = 20


def derive_seed(now: datetime) -> int:
    """Seed deterministica a partir de AAAAMMDD + bloco do dia (manha/tarde)."""
    brt = config.to_brt(now)
    bloco = 0 if (brt.hour * 60 + brt.minute) < 660 else 1
    return int(brt.strftime("%Y%m%d")) * 10 + bloco


def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    demanda = sum((r.get("demanda_sem_t") or 0.0) for r in rows)
    estoque = sum((r.get("estoque_t") or 0.0) for r in rows)
    producao = sum((r.get("producao_real_t") or 0.0) for r in rows)
    var = 0.0
    for r in rows:
        sigma = r.get("sigma_sem_t")
        if not sigma or sigma <= 0:
            sigma = config.CV_FALLBACK_FAMILIA * (r.get("demanda_sem_t") or 0.0)
        var += float(sigma) ** 2
    return {
        "demanda": demanda,
        "estoque": estoque,
        "producao": producao,
        "sigma": math.sqrt(var),
    }


def run(rows: List[Dict[str, Any]], now: datetime,
        samples: Optional[int] = None,
        issues: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """Roda a simulacao e retorna o bloco monte_carlo do contrato."""
    n = samples or config.SETTINGS.mc_samples
    seed = derive_seed(now)
    agg = _aggregate(rows)
    mu, sigma = agg["demanda"], agg["sigma"]
    estoque0 = agg["estoque"]
    producao = agg["producao"]

    # Estoque disponivel inicial do horizonte = estoque atual. A producao entra
    # como reposicao SEMANAL ao longo do horizonte (sem dupla contagem da 1a
    # semana, que inflava o estoque e subestimava a probabilidade de ruptura).
    disponivel = estoque0
    dd_mu = demanda_diaria(mu)

    coberturas, rupturas = _simulate(n, seed, mu, sigma, disponivel, producao)

    coberturas.sort()
    p5 = _percentile(coberturas, 5)
    p50 = _percentile(coberturas, 50)
    p95 = _percentile(coberturas, 95)
    p_ruptura = rupturas / n if n else 0.0

    # Histograma
    bins, hist = _histogram(coberturas, N_BINS)
    zonas = _zonas(coberturas)

    # Cross-check analitico: a cobertura media do MC deve casar com a cobertura
    # ESPERADA AO FIM do horizonte = (estoque + H*(reposicao - demanda)) / dem_dia.
    # Comparar com o estoque estatico ignoraria o drift legitimo de oferta x demanda.
    estoque_fim_esperado = max(disponivel + HORIZONTE_SEMANAS * (producao - mu), 0.0)
    analitico = estoque_fim_esperado / dd_mu if dd_mu else 0.0
    media_mc = statistics.fmean(coberturas) if coberturas else 0.0
    if issues is not None and analitico > 0:
        desvio = abs(media_mc - analitico) / analitico
        if desvio > 0.05:  # tolerancia 5%
            issues.append({
                "nivel": "warn",
                "campo": "monte_carlo.media",
                "msg": (f"media MC ({media_mc:.1f}d) diverge do analitico "
                        f"({analitico:.1f}d) em {desvio*100:.1f}% (>5%)."),
            })

    logger.info("MC: n=%d seed=%d p_ruptura=%.4f p50=%.1f", n, seed, p_ruptura, p50)
    return {
        "samples": n,
        "seed": seed,
        "p_ruptura": round(p_ruptura, 4),
        "p5_cobertura": round(p5, 1),
        "p50_cobertura": round(p50, 1),
        "p95_cobertura": round(p95, 1),
        "bins": [round(b, 1) for b in bins],
        "hist": hist,
        "zonas": zonas,
    }


def _simulate(n: int, seed: int, mu: float, sigma: float,
              disponivel: float, reposicao_sem: float):
    """Nucleo da simulacao. numpy se disponivel; senao stdlib."""
    try:
        import numpy as np  # type: ignore
        return _simulate_numpy(np, n, seed, mu, sigma, disponivel, reposicao_sem)
    except ImportError:
        return _simulate_stdlib(n, seed, mu, sigma, disponivel, reposicao_sem)


def _consume_path(demandas: List[float], disponivel: float,
                  reposicao_sem: float) -> tuple:
    """Dada uma trajetoria de demandas semanais, retorna (cobertura_dias, rompeu).

    Modelo: a cada semana o estoque recebe reposicao_sem e consome a demanda
    amostrada. Ruptura = estoque negativo em qualquer semana. Cobertura final =
    estoque_final / demanda_diaria_media.
    """
    estoque = disponivel
    rompeu = False
    total_dem = 0.0
    for d in demandas:
        estoque = estoque - d + reposicao_sem
        total_dem += d
        if estoque < 0:
            rompeu = True
    dd = (total_dem / len(demandas)) / config.DIAS_UTEIS_SEMANA if demandas else 0.0
    cobertura = estoque / dd if dd > 0 else 999.0
    cobertura = max(min(cobertura, 365.0), 0.0)
    return cobertura, rompeu


def _simulate_numpy(np, n, seed, mu, sigma, disponivel, reposicao_sem):
    rng = np.random.default_rng(seed)
    h = HORIZONTE_SEMANAS
    # matriz n x h de demandas, truncada em 0 (demanda nao-negativa)
    dem = rng.normal(mu, max(sigma, 1e-9), size=(n, h))
    np.clip(dem, 0.0, None, out=dem)
    # estoque acumulado por semana: disponivel + cumsum(reposicao - dem)
    deltas = reposicao_sem - dem
    estoque_path = disponivel + np.cumsum(deltas, axis=1)
    rompeu = (estoque_path < 0).any(axis=1)
    estoque_final = estoque_path[:, -1]
    dd = dem.mean(axis=1) / config.DIAS_UTEIS_SEMANA
    with np.errstate(divide="ignore", invalid="ignore"):
        cob = np.where(dd > 0, estoque_final / dd, 999.0)
    cob = np.clip(cob, 0.0, 365.0)
    return cob.tolist(), int(rompeu.sum())


def _simulate_stdlib(n, seed, mu, sigma, disponivel, reposicao_sem):
    import random

    rnd = random.Random(seed)
    h = HORIZONTE_SEMANAS
    coberturas: List[float] = []
    rupturas = 0
    sg = max(sigma, 1e-9)
    for _ in range(n):
        demandas = [max(rnd.gauss(mu, sg), 0.0) for _ in range(h)]
        cob, rompeu = _consume_path(demandas, disponivel, reposicao_sem)
        coberturas.append(cob)
        if rompeu:
            rupturas += 1
    return coberturas, rupturas


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def _histogram(values: List[float], n_bins: int):
    if not values:
        return [0.0] * (n_bins + 1), [0] * n_bins
    lo, hi = min(values), max(values)
    if hi == lo:
        hi = lo + 1.0
    width = (hi - lo) / n_bins
    edges = [lo + i * width for i in range(n_bins + 1)]
    counts = [0] * n_bins
    for v in values:
        idx = int((v - lo) / width)
        if idx >= n_bins:
            idx = n_bins - 1
        if idx < 0:
            idx = 0
        counts[idx] += 1
    return edges, counts


def _zonas(coberturas: List[float]) -> Dict[str, float]:
    """Fracao das amostras em cada zona: ruptura (<7), alvo (7..30), excesso (>30)."""
    n = len(coberturas) or 1
    rup = sum(1 for c in coberturas if c < config.CRITICO_DIAS)
    exc = sum(1 for c in coberturas if c > 30.0)
    alvo = n - rup - exc
    return {
        "ruptura": round(rup / n, 4),
        "alvo": round(alvo / n, 4),
        "excesso": round(exc / n, 4),
    }
