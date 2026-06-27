"""Motor de calculo S&OE (stdlib: statistics + math).

Corrige os vicios do painel HTML original:

  * Fator Z = inversa da normal (NormalDist().inv_cdf), nao tabela em degraus.
  * sigma REAL por SKU; fallback por CV de familia gera issue de validacao.
  * Safety stock dimensionalmente correto:
        ss = z * sigma_diario * sqrt(lead_time_dias)
        sigma_diario = sigma_semanal / sqrt(7)   (demanda em 7 dias corridos)
  * cobertura = estoque / demanda_diaria  (demanda_diaria = demanda_sem/7)
  * estoque_final = estoque + producao - demanda
  * gap = suprimento - demanda
  * OTIF agregado ponderado por volume; fill rate; aderencia = realizado/plano
  * MAPE = mean(|prev-real|/real); WMAPE = sum|prev-real|/sum(real);
    bias = mean((prev-real)/real)
  * utilizacao de capacidade; health_score (media ponderada das 5 dimensoes)
  * saude financeira: EBITDA projetado, capital imobilizado
  * 3 cenarios com score normalizado min-max (sem somar unidades heterogeneas)
  * excecoes priorizadas com impacto em R$ e t

Todas as funcoes principais sao PURAS (entrada -> saida, sem efeito colateral).
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import config

logger = logging.getLogger("soe.compute")

DIAS_SEMANA = config.DIAS_UTEIS_SEMANA  # 7.0
Issue = Dict[str, str]


# --------------------------------------------------------------------------- #
# Primitivas                                                                   #
# --------------------------------------------------------------------------- #
def z_from_ns(ns_pct: float) -> float:
    """Fator de seguranca Z = inversa da normal padrao para o nivel de servico.

    ns_pct em [0,100). Clampado a (0,100) para evitar inf. CORRECAO central do
    painel: usa NormalDist().inv_cdf, nao uma tabela discreta em degraus.
    """
    p = min(max(ns_pct, 0.01), 99.99) / 100.0
    return statistics.NormalDist().inv_cdf(p)


def demanda_diaria(demanda_sem: float) -> float:
    """Demanda diaria = demanda semanal / 7 (dias corridos)."""
    return demanda_sem / DIAS_SEMANA if DIAS_SEMANA else 0.0


def cobertura_dias(estoque: float, demanda_sem: float) -> float:
    """Cobertura em dias = estoque / demanda_diaria. Inf-guard -> 999 se dem=0."""
    dd = demanda_diaria(demanda_sem)
    if dd <= 0:
        return 999.0
    return estoque / dd


def safety_stock(z: float, sigma_sem: float, lead_time_dias: float) -> float:
    """Estoque de seguranca dimensionalmente correto.

    sigma_diario = sigma_semanal / sqrt(7) (variancia diaria some em 7 dias ->
    variancia semanal). ss = z * sigma_diario * sqrt(lead_time_dias).
    """
    if sigma_sem <= 0 or lead_time_dias <= 0:
        return 0.0
    sigma_diario = sigma_sem / math.sqrt(DIAS_SEMANA)
    return z * sigma_diario * math.sqrt(lead_time_dias)


def status_cobertura(cob: float) -> str:
    """critico (<7) | atencao (7..15) | saudavel (>=15)."""
    if cob < config.CRITICO_DIAS:
        return "critico"
    if cob < config.ATENCAO_DIAS:
        return "atencao"
    return "saudavel"


def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b else default


def _round(x: Optional[float], n: int = 1) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return round(float(x), n)


# --------------------------------------------------------------------------- #
# SKU level                                                                    #
# --------------------------------------------------------------------------- #
def _resolve_sigma(row: Dict[str, Any], familia_cv: Dict[str, float],
                   issues: List[Issue]) -> Tuple[float, bool]:
    """Retorna (sigma_sem, usou_fallback). Fallback por CV de familia gera issue."""
    sigma = row.get("sigma_sem_t")
    demanda = row.get("demanda_sem_t") or 0.0
    if sigma is not None and sigma > 0:
        return float(sigma), False
    cv = familia_cv.get(row.get("familia", ""), config.CV_FALLBACK_FAMILIA)
    derived = cv * demanda
    issues.append({
        "nivel": "warn",
        "campo": f"sigma_sem_t[{row.get('sku')}]",
        "msg": (f"sigma ausente; derivado de CV={cv:.2f} da familia "
                f"'{row.get('familia')}' -> {derived:.1f} t"),
    })
    return derived, True


def compute_skus(rows: List[Dict[str, Any]], ns_meta: float,
                 issues: List[Issue]) -> List[Dict[str, Any]]:
    """Calcula metricas por SKU. Lista pronta para o contrato data.json['skus']."""
    z_meta = z_from_ns(ns_meta)
    # CV medio por familia a partir dos SKUs que TEM sigma (p/ fallback coerente)
    familia_cv = _familia_cv(rows)

    out: List[Dict[str, Any]] = []
    for r in rows:
        demanda = r.get("demanda_sem_t") or 0.0
        plano = r.get("plano_t") or 0.0
        estoque = r.get("estoque_t") or 0.0
        prod = r.get("producao_real_t") or 0.0
        prev = r.get("demanda_prev_t") or 0.0
        real = r.get("demanda_real_t") or 0.0
        otif = r.get("otif_pct") or 0.0
        lt = r.get("lead_time_dias") or config.get_float("SOE_LEAD_TIME_DIAS", 14.0)
        preco = r.get("preco_rs_t") or 0.0
        ebitda_t = r.get("ebitda_rs_t") or 0.0
        custo_est = r.get("custo_estoque_rs_t") or 0.0

        sigma, _fb = _resolve_sigma(r, familia_cv, issues)
        cob = cobertura_dias(estoque, demanda)
        ss = safety_stock(z_meta, sigma, lt)

        # MAPE/bias do SKU (1 ponto -> erro percentual absoluto/sinal)
        mape = abs(prev - real) / real * 100.0 if real else 0.0
        bias = (prev - real) / real * 100.0 if real else 0.0
        aderencia = _safe_div(prod, plano) * 100.0 if plano else 0.0

        out.append({
            "sku": r.get("sku", ""),
            "familia": r.get("familia", ""),
            "bitola": r.get("bitola", ""),
            "demanda_sem_t": _round(demanda, 1),
            "plano_t": _round(plano, 1),
            "estoque_t": _round(estoque, 1),
            "producao_real_t": _round(prod, 1),
            "otif_pct": _round(otif, 1),
            "cobertura_dias": _round(cob, 1),
            "status": status_cobertura(cob),
            "mape_pct": _round(mape, 1),
            "bias_pct": _round(bias, 1),
            "aderencia_pct": _round(aderencia, 1),
            "lead_time_dias": _round(lt, 0),
            "sigma_sem_t": _round(sigma, 1),
            "safety_stock_t": _round(ss, 1),
            "preco_rs_t": _round(preco, 0),
            "ebitda_rs_t": _round(ebitda_t, 0),
            "custo_estoque_rs_t": _round(custo_est, 0),
            "sparkline": _sparkline(cob, otif),
        })
    return out


def _familia_cv(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """CV (sigma/demanda) medio por familia, usando apenas SKUs com sigma valido."""
    acc: Dict[str, List[float]] = {}
    for r in rows:
        sigma = r.get("sigma_sem_t")
        demanda = r.get("demanda_sem_t") or 0.0
        if sigma and sigma > 0 and demanda > 0:
            acc.setdefault(r.get("familia", ""), []).append(sigma / demanda)
    return {fam: statistics.fmean(v) for fam, v in acc.items() if v}


def _sparkline(cob: float, otif: float) -> List[float]:
    """6 valores plausiveis das ultimas 6 semanas (deterministico a partir do estado).

    Sem aleatoriedade: pequena rampa convergindo para o valor atual de cobertura,
    para o front desenhar tendencia. Valores arredondados.
    """
    base = min(cob, 60.0)
    steps = [base * f for f in (0.82, 0.88, 0.93, 0.97, 1.0, 1.0)]
    return [round(s, 1) for s in steps]


# --------------------------------------------------------------------------- #
# Agregados / KPIs                                                             #
# --------------------------------------------------------------------------- #
def compute_kpis(skus: List[Dict[str, Any]], rows: List[Dict[str, Any]],
                 capacidade: Dict[str, Any], ns_meta: float) -> Dict[str, Any]:
    """KPIs agregados do dashboard. OTIF/aderencia ponderados por volume."""
    vol_total = sum((r.get("demanda_sem_t") or 0.0) for r in rows) or 1.0

    otif = sum((r.get("demanda_sem_t") or 0.0) * (r.get("otif_pct") or 0.0)
               for r in rows) / vol_total
    aderencia = sum((s["demanda_sem_t"] or 0) * (s["aderencia_pct"] or 0)
                    for s in skus) / vol_total

    # fill rate = atendido / demandado (atendido = min(demanda, estoque+prod) por SKU)
    atendido = 0.0
    demandado = 0.0
    for r in rows:
        d = r.get("demanda_sem_t") or 0.0
        disp = (r.get("estoque_t") or 0.0) + (r.get("producao_real_t") or 0.0)
        atendido += min(d, disp)
        demandado += d
    fill_rate = _safe_div(atendido, demandado) * 100.0

    cob_media = statistics.fmean([s["cobertura_dias"] for s in skus]) if skus else 0.0
    skus_alerta = sum(1 for s in skus if s["status"] in ("critico", "atencao"))

    suprimento = sum((r.get("producao_real_t") or 0.0) for r in rows)
    demanda = sum((r.get("demanda_sem_t") or 0.0) for r in rows)
    gap_kt = (suprimento - demanda) / 1000.0

    acc = compute_acuracidade_global(rows)
    mape = acc["mape_pct"]
    bias = acc["bias_pct"]

    # Financeiro
    # EBITDA projetado = margem sobre o VENDIDO (limitado a demanda e a
    # disponibilidade estoque+producao); producao que vira estoque NAO gera EBITDA.
    ebitda_proj_mi = sum(
        min((r.get("estoque_t") or 0.0) + (r.get("producao_real_t") or 0.0),
            (r.get("demanda_sem_t") or 0.0)) * (r.get("ebitda_rs_t") or 0.0)
        for r in rows) / 1e6
    # capital imobilizado = soma estoque_t * preco_rs_t / 1e6 (em milhoes)
    capital_mi = sum((r.get("estoque_t") or 0.0) * (r.get("preco_rs_t") or 0.0)
                     for r in rows) / 1e6

    util_cap = capacidade["util_pct_global"]

    radar = compute_radar(otif, cob_media, util_cap, mape, capital_mi, ebitda_proj_mi)
    health = compute_health_score(radar)

    return {
        "otif_pct": _round(otif, 1),
        "fill_rate_pct": _round(fill_rate, 1),
        "aderencia_plano_pct": _round(aderencia, 1),
        "cobertura_media_dias": _round(cob_media, 1),
        "skus_alerta": skus_alerta,
        "gap_demanda_suprimento_kt": _round(gap_kt, 1),
        "ebitda_projetado_mi": _round(ebitda_proj_mi, 1),
        "capital_imobilizado_mi": _round(capital_mi, 1),
        "mape_pct": _round(mape, 1),
        "bias_pct": _round(bias, 1),
        "utilizacao_capacidade_pct": _round(util_cap, 1),
        "health_score": int(round(health)),
        "health_status": health_status(health),
        "_radar": radar,  # interno, removido apos montar payload
    }


def compute_acuracidade_global(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """MAPE (media simples) e bias (media simples) global em %.

    Tambem expoe WMAPE (ponderado por volume real) p/ quem precisar.
    """
    mapes, biases = [], []
    sum_abs_err = 0.0
    sum_real = 0.0
    for r in rows:
        prev = r.get("demanda_prev_t") or 0.0
        real = r.get("demanda_real_t") or 0.0
        if real > 0:
            mapes.append(abs(prev - real) / real * 100.0)
            biases.append((prev - real) / real * 100.0)
            sum_abs_err += abs(prev - real)
            sum_real += real
    return {
        "mape_pct": statistics.fmean(mapes) if mapes else 0.0,
        "bias_pct": statistics.fmean(biases) if biases else 0.0,
        "wmape_pct": _safe_div(sum_abs_err, sum_real) * 100.0,
    }


def compute_capacidade(linhas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Capacidades de linha + utilizacao global. Robusto a lista vazia."""
    out_linhas = []
    cap_total = 0.0
    util_total = 0.0
    for l in linhas:
        cap = float(l.get("capacidade_t") or 0.0)
        usado = float(l.get("utilizado_t") or 0.0)
        util_pct = _safe_div(usado, cap) * 100.0 if cap else 0.0
        out_linhas.append({
            "linha": l.get("linha", ""),
            "capacidade_t": _round(cap, 0),
            "utilizado_t": _round(usado, 0),
            "util_pct": _round(util_pct, 1),
        })
        cap_total += cap
        util_total += usado
    util_global = _safe_div(util_total, cap_total) * 100.0 if cap_total else 0.0
    return {"linhas": out_linhas, "util_pct_global": util_global}


# --------------------------------------------------------------------------- #
# Radar e health                                                               #
# --------------------------------------------------------------------------- #
def compute_radar(otif: float, cob_media: float, util_cap: float, mape: float,
                  capital_mi: float, ebitda_mi: float) -> Dict[str, int]:
    """As 5 dimensoes do radar normalizadas em 0..100 (clampadas)."""
    def clamp(x: float) -> int:
        return int(round(min(max(x, 0.0), 100.0)))

    servico = otif  # ja em %
    # estoque: alvo ~18 dias. Penaliza muito baixo (ruptura) e muito alto (excesso)
    alvo = 18.0
    estoque = 100.0 - min(abs(cob_media - alvo) / alvo, 1.0) * 100.0
    # capacidade: util ideal ~85-90%; penaliza ocioso e overload
    ideal = 87.0
    capacidade = 100.0 - min(abs(util_cap - ideal) / ideal, 1.0) * 100.0
    # acuracidade: 100 - MAPE (clampado)
    acuracidade = 100.0 - mape
    # caixa: relacao EBITDA/capital imobilizado (giro). Normaliza p/ 0..100.
    giro = _safe_div(ebitda_mi, capital_mi)  # ~0.1..0.2 tipico
    caixa = min(giro / 0.20, 1.0) * 100.0

    return {
        "servico": clamp(servico),
        "estoque": clamp(estoque),
        "capacidade": clamp(capacidade),
        "acuracidade": clamp(acuracidade),
        "caixa": clamp(caixa),
    }


def compute_health_score(radar: Dict[str, int]) -> float:
    """Media ponderada das 5 dimensoes (pesos em config.HEALTH_WEIGHTS)."""
    w = config.HEALTH_WEIGHTS
    total_w = sum(w.values()) or 1.0
    return sum(radar[k] * w[k] for k in w) / total_w


def health_status(score: float) -> str:
    """verde >=80 ; amarelo 60..79 ; vermelho <60."""
    if score >= 80:
        return "verde"
    if score >= 60:
        return "amarelo"
    return "vermelho"


# --------------------------------------------------------------------------- #
# Balanco semanal                                                             #
# --------------------------------------------------------------------------- #
def compute_balanco(rows: List[Dict[str, Any]], now: datetime,
                    semanas: int = config.SEMANAS_HORIZONTE) -> List[Dict[str, Any]]:
    """Projecao de demanda x suprimento por semana (kt) e cobertura.

    Modelo deterministico: a partir do estado atual, projeta o estoque rolando
    estoque_{t} = estoque_{t-1} + suprimento_t - demanda_t. Demanda/suprimento
    constantes (semana base) -> deterministico e reprodutivel.
    """
    demanda_t = sum((r.get("demanda_sem_t") or 0.0) for r in rows)
    suprimento_t = sum((r.get("producao_real_t") or 0.0) for r in rows)
    estoque_t = sum((r.get("estoque_t") or 0.0) for r in rows)

    week0 = now.isocalendar()[1] if hasattr(now, "isocalendar") else 26
    out = []
    estoque = estoque_t
    for i in range(semanas):
        estoque = estoque + suprimento_t - demanda_t
        cob = cobertura_dias(max(estoque, 0.0), demanda_t)
        out.append({
            "semana": f"S{week0 + i:02d}",
            "demanda_kt": _round(demanda_t / 1000.0, 1),
            "suprimento_kt": _round(suprimento_t / 1000.0, 1),
            "cobertura_dias": _round(cob, 1),
        })
    return out


# --------------------------------------------------------------------------- #
# Acuracidade por familia                                                     #
# --------------------------------------------------------------------------- #
def compute_acuracidade_familias(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """MAPE/bias por familia (ponderado WMAPE) + planejado/realizado agregados."""
    fam: Dict[str, Dict[str, float]] = {}
    for r in rows:
        f = r.get("familia", "")
        d = fam.setdefault(f, {"abs_err": 0.0, "sum_real": 0.0, "sum_signed": 0.0,
                               "plan": 0.0, "real": 0.0, "n": 0.0})
        prev = r.get("demanda_prev_t") or 0.0
        real = r.get("demanda_real_t") or 0.0
        plan = r.get("plano_t") or 0.0
        d["plan"] += plan
        d["real"] += real
        if real > 0:
            d["abs_err"] += abs(prev - real)
            d["sum_real"] += real
            d["sum_signed"] += (prev - real)
            d["n"] += 1
    out = []
    for f, d in sorted(fam.items()):
        mape = _safe_div(d["abs_err"], d["sum_real"]) * 100.0
        bias = _safe_div(d["sum_signed"], d["sum_real"]) * 100.0
        out.append({
            "familia": f,
            "mape_pct": _round(mape, 1),
            "bias_pct": _round(bias, 1),
            "planejado_t": _round(d["plan"], 0),
            "realizado_t": _round(d["real"], 0),
        })
    return out


# --------------------------------------------------------------------------- #
# Excecoes priorizadas                                                        #
# --------------------------------------------------------------------------- #
def compute_excecoes(skus: List[Dict[str, Any]], rows_by_sku: Dict[str, Dict[str, Any]],
                     capacidade: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Lista de excecoes priorizada por impacto financeiro (desc).

    Tipos: ruptura (cob<7), excesso (cob>alvo*1.5), capacidade (util>100%),
    atraso (otif baixo). Impacto em R$ e t.
    """
    alvo = config.ATENCAO_DIAS  # 15 dias de referencia p/ excesso
    exc: List[Dict[str, Any]] = []

    for s in skus:
        sku = s["sku"]
        row = rows_by_sku.get(sku, {})
        preco = row.get("preco_rs_t") or 0.0
        custo_est = row.get("custo_estoque_rs_t") or 0.0
        ebitda_t = row.get("ebitda_rs_t") or 0.0
        demanda = s["demanda_sem_t"] or 0.0
        estoque = s["estoque_t"] or 0.0
        cob = s["cobertura_dias"] or 0.0
        dd = demanda_diaria(demanda)

        if cob < config.CRITICO_DIAS:
            # t faltantes para chegar ao alvo de cobertura
            falta_t = max(dd * config.ATENCAO_DIAS - estoque, 0.0)
            exc.append(_exc(sku, s["familia"], "ruptura", "alta" if cob < 4 else "media",
                            falta_t * ebitda_t, falta_t, cob,
                            f"Acelerar producao/transferencia de {falta_t:.0f} t; "
                            f"cobertura {cob:.1f}d < {config.CRITICO_DIAS:.0f}d."))
        elif cob > alvo * config.EXCESSO_FATOR:
            excesso_t = max(estoque - dd * alvo, 0.0)
            exc.append(_exc(sku, s["familia"], "excesso", "media" if cob < alvo * 2 else "baixa",
                            excesso_t * custo_est, excesso_t, cob,
                            f"Reduzir reposicao/realocar {excesso_t:.0f} t; "
                            f"cobertura {cob:.1f}d > {alvo * config.EXCESSO_FATOR:.0f}d."))

        if (s["otif_pct"] or 0.0) < 90.0:
            falta_t = demanda * (90.0 - s["otif_pct"]) / 100.0
            exc.append(_exc(sku, s["familia"], "atraso", "media", falta_t * ebitda_t,
                            falta_t, cob,
                            f"OTIF {s['otif_pct']:.1f}% < 90%; revisar sequenciamento/expedicao."))

    # capacidade: por linha sobrecarregada
    for l in capacidade["linhas"]:
        if (l["util_pct"] or 0.0) > 100.0:
            over_t = (l["utilizado_t"] or 0.0) - (l["capacidade_t"] or 0.0)
            exc.append(_exc(l["linha"], "Capacidade", "capacidade", "alta",
                            0.0, over_t, 0.0,
                            f"Linha {l['linha']} a {l['util_pct']:.0f}% (>100%); "
                            f"realocar {over_t:.0f} t ou abrir turno."))

    exc.sort(key=lambda e: (e["impacto_rs"] or 0.0), reverse=True)
    return exc


def _exc(sku: str, familia: str, tipo: str, sev: str, impacto_rs: float,
         impacto_t: float, cob: float, acao: str) -> Dict[str, Any]:
    return {
        "sku": sku,
        "familia": familia,
        "tipo": tipo,
        "severidade": sev,
        "impacto_rs": _round(impacto_rs, 0),
        "impacto_t": _round(impacto_t, 1),
        "cobertura_dias": _round(cob, 1),
        "acao": acao,
    }


# --------------------------------------------------------------------------- #
# Cenarios (score normalizado, sem somar unidades heterogeneas)               #
# --------------------------------------------------------------------------- #
# Pesos por cenario sobre criterios normalizados: (servico, ebitda, -custo)
_CENARIO_DEFS = [
    ("Servico 99%",   99.0, {"servico": 0.60, "ebitda": 0.20, "custo": 0.20}),
    ("Rentabilidade", 92.0, {"servico": 0.20, "ebitda": 0.55, "custo": 0.25}),
    ("Equilibrio",    96.0, {"servico": 0.40, "ebitda": 0.35, "custo": 0.25}),
]


def compute_cenarios(rows: List[Dict[str, Any]], ns_meta: float) -> List[Dict[str, Any]]:
    """3 cenarios com score min-max 0..100 por criterio. Recomenda o maior.

    Para cada cenario calculamos producao p/ atingir o NS (estoque-alvo = ss
    dimensional), estoque final, cobertura, EBITDA projetado, custo adicional e
    caixa imobilizado. Depois normalizamos cada criterio (min-max) entre os 3
    cenarios e somamos PONDERADO -> nenhuma soma de unidades heterogeneas.
    """
    demanda_total = sum((r.get("demanda_sem_t") or 0.0) for r in rows)
    estoque_atual = sum((r.get("estoque_t") or 0.0) for r in rows)

    raw = []
    for nome, ns, pesos in _CENARIO_DEFS:
        z = z_from_ns(ns)
        producao = 0.0
        estoque_final = 0.0
        ebitda = 0.0
        custo_add = 0.0
        caixa = 0.0
        for r in rows:
            demanda = r.get("demanda_sem_t") or 0.0
            estoque = r.get("estoque_t") or 0.0
            lt = r.get("lead_time_dias") or 14.0
            sigma = r.get("sigma_sem_t") or (config.CV_FALLBACK_FAMILIA * demanda)
            preco = r.get("preco_rs_t") or 0.0
            ebitda_t = r.get("ebitda_rs_t") or 0.0
            custo_est = r.get("custo_estoque_rs_t") or 0.0

            ss = safety_stock(z, sigma, lt)
            alvo = demanda + ss                       # estoque-alvo do periodo
            prod = max(demanda + alvo - estoque, 0.0)  # repor p/ atingir alvo
            ef = estoque + prod - demanda

            producao += prod
            estoque_final += ef
            ebitda += min(estoque + prod, demanda) * ebitda_t  # margem sobre o vendido
            caixa += ef * preco
            custo_add += max(ef - estoque, 0.0) * custo_est

        dd = demanda_diaria(demanda_total)
        cob = _safe_div(estoque_final, dd) if dd else 999.0
        raw.append({
            "nome": nome, "ns": ns, "z": z,
            "producao_t": producao, "estoque_final_t": estoque_final,
            "cobertura_dias": cob,
            "ebitda_proj_mi": ebitda / 1e6,
            "ebitda_rs_t": _safe_div(ebitda, producao),
            "custo_adicional_mi": custo_add / 1e6,
            "caixa_imob_mi": caixa / 1e6,
            "pesos": pesos,
        })

    # Normalizacao min-max por criterio entre os 3 cenarios
    def norm(values: List[float], invert: bool = False) -> List[float]:
        lo, hi = min(values), max(values)
        rng = hi - lo
        if rng == 0:
            return [50.0 for _ in values]
        scaled = [(v - lo) / rng * 100.0 for v in values]
        return [100.0 - s for s in scaled] if invert else scaled

    n_serv = norm([c["ns"] for c in raw])
    n_ebitda = norm([c["ebitda_proj_mi"] for c in raw])
    n_custo = norm([c["custo_adicional_mi"] for c in raw], invert=True)  # menos custo = melhor

    out = []
    for c, sv, eb, cu in zip(raw, n_serv, n_ebitda, n_custo):
        p = c["pesos"]
        score = sv * p["servico"] + eb * p["ebitda"] + cu * p["custo"]
        out.append({
            "nome": c["nome"],
            "ns_pct": _round(c["ns"], 1),
            "z": _round(c["z"], 2),
            "producao_t": _round(c["producao_t"], 0),
            "estoque_final_t": _round(c["estoque_final_t"], 0),
            "cobertura_dias": _round(c["cobertura_dias"], 1),
            "ebitda_rs_t": _round(c["ebitda_rs_t"], 0),
            "ebitda_proj_mi": _round(c["ebitda_proj_mi"], 1),
            "custo_adicional_mi": _round(c["custo_adicional_mi"], 1),
            "caixa_imob_mi": _round(c["caixa_imob_mi"], 1),
            "score": _round(score, 1),
            "recomendado": False,
            "motivo": "",
        })

    out.sort(key=lambda c: c["score"], reverse=True)
    best = out[0]
    best["recomendado"] = True
    best["motivo"] = (f"Maior score ({best['score']}) combinando servico, EBITDA "
                      f"e custo conforme pesos do cenario.")
    for c in out[1:]:
        c["motivo"] = f"Score {c['score']} abaixo do recomendado ({best['score']})."
    return out


# --------------------------------------------------------------------------- #
# Fluxo Sankey-like                                                           #
# --------------------------------------------------------------------------- #
def compute_fluxo(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Balanco material: demanda, producao, estoque, atendimento, perda."""
    demanda = sum((r.get("demanda_sem_t") or 0.0) for r in rows)
    producao = sum((r.get("producao_real_t") or 0.0) for r in rows)
    estoque = sum((r.get("estoque_t") or 0.0) for r in rows)
    atendimento = 0.0
    for r in rows:
        d = r.get("demanda_sem_t") or 0.0
        disp = (r.get("estoque_t") or 0.0) + (r.get("producao_real_t") or 0.0)
        atendimento += min(d, disp)
    perda = max(demanda - atendimento, 0.0)  # demanda nao atendida
    return {
        "demanda_t": _round(demanda, 0),
        "producao_t": _round(producao, 0),
        "estoque_t": _round(estoque, 0),
        "atendimento_t": _round(atendimento, 0),
        "perda_t": _round(perda, 0),
    }
