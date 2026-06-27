"""Bateria de double-check do payload data.json.

Cada check e uma funcao que adiciona issues a uma lista. Severidade:
  - 'error' -> incoerencia matematica/estrutural (rompe a confianca do dash);
  - 'warn'  -> tolerancia excedida, fallback usado, ou variacao suspeita.

Status final: 'error' se houver qualquer error; 'warn' se houver apenas warns;
'ok' caso contrario.

Tambem calcula meta.delta comparando com o data.json anterior.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional

from . import config
from .compute import demanda_diaria

logger = logging.getLogger("soe.validate")

Issue = Dict[str, str]
TOL_REL = 0.02   # 2% de tolerancia relativa
TOL_ABS = 0.5    # 0.5 t / 0.5 ponto absoluto


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))


def _err(issues: List[Issue], campo: str, msg: str) -> None:
    issues.append({"nivel": "error", "campo": campo, "msg": msg})


def _warn(issues: List[Issue], campo: str, msg: str) -> None:
    issues.append({"nivel": "warn", "campo": campo, "msg": msg})


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(TOL_ABS, abs(b) * TOL_REL)


# --------------------------------------------------------------------------- #
# Checks individuais (retornam quantos checks executaram)                      #
# --------------------------------------------------------------------------- #
def _check_no_bad_numbers(payload: Dict[str, Any], issues: List[Issue]) -> int:
    """Sem NaN/inf/None em campos numericos criticos; percentuais em [0,100]."""
    checks = 0
    kpis = payload.get("kpis", {})
    for k, v in kpis.items():
        if k in ("health_status", "deltas", "_radar"):
            continue
        checks += 1
        if not _is_num(v):
            _err(issues, f"kpis.{k}", f"valor nao-numerico: {v!r}")
    # Percentuais LIMITADOS a [0,100] por definicao (taxas de atendimento).
    bounded_fields = ["otif_pct", "fill_rate_pct"]
    for f in bounded_fields:
        checks += 1
        v = kpis.get(f)
        if _is_num(v) and not (0.0 <= v <= 100.0):
            _err(issues, f"kpis.{f}", f"percentual fora de [0,100]: {v}")
    # Percentuais que SAO razoes e podem ultrapassar 100% legitimamente
    # (aderencia = realizado/plano com superproducao; utilizacao = overload).
    # Aqui exigimos apenas nao-negatividade; overload >100% vira excecao.
    ratio_fields = ["aderencia_plano_pct", "utilizacao_capacidade_pct"]
    for f in ratio_fields:
        checks += 1
        v = kpis.get(f)
        if _is_num(v) and v < 0.0:
            _err(issues, f"kpis.{f}", f"razao negativa indevida: {v}")
        elif _is_num(v) and v > 130.0:
            _warn(issues, f"kpis.{f}", f"razao muito acima de 100%: {v}")
    return checks


def _check_skus(payload: Dict[str, Any], issues: List[Issue]) -> int:
    """Coerencia por SKU: cobertura, status, MAPE>=0, otif em [0,100]."""
    checks = 0
    for s in payload.get("skus", []):
        sku = s.get("sku", "?")
        demanda = s.get("demanda_sem_t") or 0.0
        estoque = s.get("estoque_t") or 0.0
        cob = s.get("cobertura_dias")

        checks += 1
        dd = demanda_diaria(demanda)
        if dd > 0 and _is_num(cob):
            esperado = estoque / dd
            if not _close(cob, esperado):
                _err(issues, f"skus[{sku}].cobertura_dias",
                     f"cobertura {cob} != estoque/dem_dia {esperado:.2f}")

        checks += 1
        if _is_num(cob):
            st_esperado = ("critico" if cob < config.CRITICO_DIAS
                           else "atencao" if cob < config.ATENCAO_DIAS else "saudavel")
            if s.get("status") != st_esperado:
                _err(issues, f"skus[{sku}].status",
                     f"status '{s.get('status')}' incoerente com cobertura {cob} "
                     f"(esperado '{st_esperado}')")

        checks += 1
        mape = s.get("mape_pct")
        if _is_num(mape) and mape < 0:
            _err(issues, f"skus[{sku}].mape_pct", f"MAPE negativo: {mape}")

        checks += 1
        otif = s.get("otif_pct")
        if _is_num(otif) and not (0.0 <= otif <= 100.0):
            _err(issues, f"skus[{sku}].otif_pct", f"OTIF fora de [0,100]: {otif}")

        checks += 1
        for f in ("estoque_t", "demanda_sem_t", "safety_stock_t"):
            v = s.get(f)
            if _is_num(v) and v < 0:
                _err(issues, f"skus[{sku}].{f}", f"valor negativo indevido: {v}")
    return checks


def _check_health(payload: Dict[str, Any], issues: List[Issue]) -> int:
    """health_status coerente com health_score."""
    kpis = payload.get("kpis", {})
    score = kpis.get("health_score")
    status = kpis.get("health_status")
    if not _is_num(score):
        _err(issues, "kpis.health_score", "ausente/nao-numerico")
        return 1
    esperado = "verde" if score >= 80 else "amarelo" if score >= 60 else "vermelho"
    if status != esperado:
        _err(issues, "kpis.health_status",
             f"'{status}' incoerente com score {score} (esperado '{esperado}')")
    return 1


def _check_z(payload: Dict[str, Any], issues: List[Issue]) -> int:
    """z dos cenarios coerente com ns (monotonico: ns maior -> z maior)."""
    from .compute import z_from_ns

    checks = 0
    for c in payload.get("cenarios", []):
        checks += 1
        ns = c.get("ns_pct")
        z = c.get("z")
        if _is_num(ns) and _is_num(z):
            esperado = z_from_ns(ns)
            if abs(z - esperado) > 0.02:
                _err(issues, f"cenarios[{c.get('nome')}].z",
                     f"z {z} != inv_cdf(ns={ns}) {esperado:.3f}")
    return checks


def _check_montecarlo(payload: Dict[str, Any], issues: List[Issue]) -> int:
    """MC: p_ruptura em [0,1]; p5<=p50<=p95; zonas somam ~1; samples esperado."""
    checks = 0
    mc = payload.get("monte_carlo", {})

    checks += 1
    pr = mc.get("p_ruptura")
    if _is_num(pr) and not (0.0 <= pr <= 1.0):
        _err(issues, "monte_carlo.p_ruptura", f"fora de [0,1]: {pr}")

    checks += 1
    p5, p50, p95 = mc.get("p5_cobertura"), mc.get("p50_cobertura"), mc.get("p95_cobertura")
    if all(_is_num(x) for x in (p5, p50, p95)) and not (p5 <= p50 <= p95):
        _err(issues, "monte_carlo.percentis",
             f"p5<=p50<=p95 violado: {p5}/{p50}/{p95}")

    checks += 1
    zonas = mc.get("zonas", {})
    soma = sum(v for v in zonas.values() if _is_num(v))
    if zonas and not _close(soma, 1.0):
        _err(issues, "monte_carlo.zonas", f"zonas somam {soma:.3f} != 1.0")

    checks += 1
    if mc.get("samples") != config.SETTINGS.mc_samples:
        _warn(issues, "monte_carlo.samples",
              f"samples={mc.get('samples')} != configurado {config.SETTINGS.mc_samples}")

    # coerencia ruptura x p50 cobertura: p_ruptura alto deve casar com p50 baixo
    checks += 1
    if _is_num(pr) and _is_num(p50):
        if pr > 0.5 and p50 > config.ATENCAO_DIAS:
            _warn(issues, "monte_carlo.coerencia",
                  f"p_ruptura {pr} alto mas p50 cobertura {p50} folgado")
    return checks


def _check_balanco(payload: Dict[str, Any], issues: List[Issue]) -> int:
    """Balanco: 8..13 semanas; demanda/suprimento nao-negativos."""
    bal = payload.get("balanco_semanas", [])
    checks = 1
    if not (8 <= len(bal) <= 13):
        _warn(issues, "balanco_semanas", f"esperado 8..13 semanas, tem {len(bal)}")
    for b in bal:
        checks += 1
        for f in ("demanda_kt", "suprimento_kt"):
            v = b.get(f)
            if _is_num(v) and v < 0:
                _err(issues, f"balanco_semanas[{b.get('semana')}].{f}",
                     f"negativo: {v}")
    return checks


def _check_reconciliacao(payload: Dict[str, Any], rows: List[Dict[str, Any]],
                         issues: List[Issue]) -> int:
    """Somatorios reconciliam: fluxo.demanda_t ~ soma demanda dos SKUs."""
    checks = 0
    fluxo = payload.get("fluxo", {})
    soma_demanda = sum((r.get("demanda_sem_t") or 0.0) for r in rows)
    soma_prod = sum((r.get("producao_real_t") or 0.0) for r in rows)

    checks += 1
    if _is_num(fluxo.get("demanda_t")) and not _close(fluxo["demanda_t"], soma_demanda):
        _err(issues, "fluxo.demanda_t",
             f"{fluxo['demanda_t']} != soma SKUs {soma_demanda:.1f}")

    checks += 1
    if _is_num(fluxo.get("producao_t")) and not _close(fluxo["producao_t"], soma_prod):
        _err(issues, "fluxo.producao_t",
             f"{fluxo['producao_t']} != soma SKUs {soma_prod:.1f}")

    # atendimento <= demanda ; perda = demanda - atendimento
    checks += 1
    at = fluxo.get("atendimento_t")
    perda = fluxo.get("perda_t")
    if all(_is_num(x) for x in (at, perda, fluxo.get("demanda_t"))):
        if not _close(at + perda, fluxo["demanda_t"]):
            _err(issues, "fluxo.perda_t",
                 f"atendimento+perda ({at}+{perda}) != demanda {fluxo['demanda_t']}")
    return checks


def _check_cenarios(payload: Dict[str, Any], issues: List[Issue]) -> int:
    """Exatamente 1 recomendado; ordenados por score desc; 3 cenarios."""
    cen = payload.get("cenarios", [])
    checks = 1
    if len(cen) != 3:
        _warn(issues, "cenarios", f"esperado 3 cenarios, tem {len(cen)}")
    rec = [c for c in cen if c.get("recomendado")]
    checks += 1
    if len(rec) != 1:
        _err(issues, "cenarios.recomendado", f"esperado 1 recomendado, tem {len(rec)}")
    checks += 1
    scores = [c.get("score") for c in cen if _is_num(c.get("score"))]
    if scores != sorted(scores, reverse=True):
        _err(issues, "cenarios.ordem", "cenarios nao ordenados por score desc")
    if rec and scores and rec[0].get("score") != max(scores):
        _err(issues, "cenarios.recomendado", "recomendado nao tem o maior score")
    return checks


# --------------------------------------------------------------------------- #
# Delta vs execucao anterior                                                  #
# --------------------------------------------------------------------------- #
def compute_delta(payload: Dict[str, Any],
                  previous: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """meta.delta: variacoes de KPIs-chave vs execucao anterior + resumo."""
    kpis = payload.get("kpis", {})
    if not previous:
        return {
            "otif_pp": 0.0,
            "cobertura_dias": 0.0,
            "skus_alerta": 0,
            "resumo": "Primeira execucao registrada (sem base anterior para comparar).",
        }
    pk = previous.get("kpis", {})

    def d(field: str) -> float:
        a = kpis.get(field)
        b = pk.get(field)
        if _is_num(a) and _is_num(b):
            return round(a - b, 2)
        return 0.0

    otif_pp = d("otif_pct")
    cob_d = d("cobertura_media_dias")
    alerta_d = int((kpis.get("skus_alerta") or 0) - (pk.get("skus_alerta") or 0))

    partes = []
    if otif_pp:
        partes.append(f"OTIF {'+' if otif_pp >= 0 else ''}{otif_pp} pp")
    if cob_d:
        partes.append(f"cobertura {'+' if cob_d >= 0 else ''}{cob_d} d")
    if alerta_d:
        partes.append(f"{'+' if alerta_d >= 0 else ''}{alerta_d} SKU(s) em alerta")
    resumo = "; ".join(partes) if partes else "Sem variacoes relevantes vs execucao anterior."

    return {
        "otif_pp": otif_pp,
        "cobertura_dias": cob_d,
        "skus_alerta": alerta_d,
        "resumo": resumo,
    }


# --------------------------------------------------------------------------- #
# Orquestrador da validacao                                                    #
# --------------------------------------------------------------------------- #
def validate(payload: Dict[str, Any], rows: List[Dict[str, Any]],
             prior_issues: Optional[List[Issue]] = None) -> Dict[str, Any]:
    """Roda todos os checks e retorna o bloco meta.validation.

    prior_issues: issues geradas durante o compute/MC (ex: fallback de sigma),
    incorporadas ao relatorio.
    """
    issues: List[Issue] = list(prior_issues or [])
    total = 0
    total += _check_no_bad_numbers(payload, issues)
    total += _check_skus(payload, issues)
    total += _check_health(payload, issues)
    total += _check_z(payload, issues)
    total += _check_montecarlo(payload, issues)
    total += _check_balanco(payload, issues)
    total += _check_reconciliacao(payload, rows, issues)
    total += _check_cenarios(payload, issues)

    errors = sum(1 for i in issues if i["nivel"] == "error")
    warns = sum(1 for i in issues if i["nivel"] == "warn")
    status = "error" if errors else ("warn" if warns else "ok")
    passed = total - errors  # warns nao reprovam checks estruturais

    logger.info("validacao: status=%s checks=%d passed=%d errors=%d warns=%d",
                status, total, passed, errors, warns)
    return {
        "status": status,
        "checks_total": total,
        "checks_passed": passed,
        "issues": issues,
    }
