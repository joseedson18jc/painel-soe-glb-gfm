#!/usr/bin/env python3
"""Orquestrador ETL do painel S&OE GLB-GFM.

Pipeline:
  1. extrai parametros (sources.get_dataframe)
  2. calcula TODAS as metricas (compute) + Monte Carlo (montecarlo)
  3. monta o payload conforme o CONTRATO data.json
  4. valida (double-check) -> meta.validation ; calcula meta.delta vs anterior
  5. grava data.json (atomico)
  6. envia e-mail de alerta (notify) [a menos que --no-email/--dry-run]
  7. publica no GitHub (publish) [a menos que --no-publish/--dry-run]

Roda 2x/dia (08:00 e 13:59 BRT) via agendador (cron/launchd).

Exemplos:
  python3 soe_etl.py --source sqlite --dry-run --verbose
  python3 soe_etl.py --source sqlite --no-publish
  python3 soe_etl.py            # usa SOE_SOURCE do .env
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# Permite rodar como script (python3 soe_etl.py) e como modulo.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from soe import (  # noqa: E402
    compute,
    config,
    montecarlo,
    notify,
    publish,
    sources,
    state,
    validate,
)

logger = logging.getLogger("soe.etl")
SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Logging                                                                      #
# --------------------------------------------------------------------------- #
def setup_logging(verbose: bool) -> None:
    config.ensure_dirs()
    level = logging.DEBUG if verbose else logging.INFO
    log_file = config.LOG_DIR / f"etl-{config.fmt_brt(config.now_utc())[:10]}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    root = logging.getLogger("soe")
    root.setLevel(level)
    root.handlers.clear()

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # symlink/copia estavel para o ultimo log (etl.log)
    try:
        stable = config.LOG_DIR / "etl.log"
        if stable.exists() or stable.is_symlink():
            stable.unlink()
        stable.symlink_to(log_file.name)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Montagem do payload                                                          #
# --------------------------------------------------------------------------- #
def build_payload(raw: Dict[str, Any], now: datetime, version: int,
                  source: str) -> Dict[str, Any]:
    """Constroi o payload completo conforme o contrato data.json."""
    rows: List[Dict[str, Any]] = raw["skus"]
    rows_by_sku = {r.get("sku"): r for r in rows}
    ns_meta = _ns_meta(raw)
    issues: List[Dict[str, str]] = []

    # SKUs e capacidade
    skus = compute.compute_skus(rows, ns_meta, issues)
    capacidade = compute.compute_capacidade(raw.get("linhas", []))

    # KPIs (consomem capacidade.util_pct_global e radar interno)
    kpis = compute.compute_kpis(skus, rows, capacidade, ns_meta)
    radar = kpis.pop("_radar")
    kpis["deltas"] = {  # preenchidos depois pelo delta global (placeholder coerente)
        "otif_pct": 0.0, "cobertura_media_dias": 0.0,
        "aderencia_plano_pct": 0.0, "ebitda_projetado_mi": 0.0,
    }

    # Blocos analiticos
    balanco = compute.compute_balanco(rows, now)
    acur_fam = compute.compute_acuracidade_familias(rows)
    excecoes = compute.compute_excecoes(skus, rows_by_sku, capacidade)
    cenarios = compute.compute_cenarios(rows, ns_meta)
    fluxo = compute.compute_fluxo(rows)
    mc = montecarlo.run(rows, now, issues=issues)
    cadencia = build_cadencia(now)

    payload: Dict[str, Any] = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": config.iso_utc(now),
            "generated_at_brt": config.fmt_brt(now),
            "run_label": config.run_label(now),
            "week": config.iso_week_label(now),
            "version": version,
            "source": source,
            "monte_carlo_samples": mc["samples"],
            "validation": {},   # preenchido apos validate
            "delta": {},        # preenchido apos compute_delta
        },
        "kpis": kpis,
        "radar": radar,
        "skus": skus,
        "balanco_semanas": balanco,
        "capacidade_linhas": capacidade["linhas"],
        "exececoes": excecoes,                  # grafia conforme contrato
        "acuracidade_familias": acur_fam,
        "cenarios": cenarios,
        "monte_carlo": mc,
        "fluxo": fluxo,
        "cadencia": cadencia,
        "_compute_issues": issues,              # interno, removido antes de gravar
    }
    return payload


def build_cadencia(now: datetime) -> List[Dict[str, Any]]:
    """Cadencia S&OE da semana corrente (eventos do ciclo)."""
    semana = config.short_week_id(now)
    data = config.ddmm(now)
    eventos = [
        ("Demand Review", "Planejamento de Demanda"),
        ("Supply Review", "Planejamento de Suprimentos"),
        ("Reconciliacao", "S&OP/S&OE Lead"),
        ("Exception Review", "Torre de Controle"),
    ]
    return [
        {"semana": semana, "evento": ev, "responsavel": resp, "data": data}
        for ev, resp in eventos
    ]


def _ns_meta(raw: Dict[str, Any]) -> float:
    try:
        return float(raw.get("meta", {}).get("ns_meta_pct", config.SETTINGS.ns_meta))
    except (TypeError, ValueError):
        return config.SETTINGS.ns_meta


def _fill_kpi_deltas(payload: Dict[str, Any], previous) -> None:
    """Preenche kpis.deltas (subset de campos) a partir do anterior."""
    if not previous:
        return
    pk = previous.get("kpis", {})
    k = payload["kpis"]
    for f in ("otif_pct", "cobertura_media_dias", "aderencia_plano_pct",
              "ebitda_projetado_mi"):
        a, b = k.get(f), pk.get(f)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            k["deltas"][f] = round(a - b, 2)


# --------------------------------------------------------------------------- #
# Gravacao atomica                                                            #
# --------------------------------------------------------------------------- #
def write_json_atomic(payload: Dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), prefix=".data-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# --------------------------------------------------------------------------- #
# Pipeline                                                                     #
# --------------------------------------------------------------------------- #
def run_pipeline(args: argparse.Namespace) -> int:
    now = config.now_utc()
    source = (args.source or config.SETTINGS.source).lower()
    out_path = Path(args.out).resolve()
    logger.info("=== ETL S&OE inicio | source=%s out=%s dry_run=%s ===",
                source, out_path, args.dry_run)

    # 1. Extracao
    raw = sources.get_dataframe(source)

    # estado anterior -> versao + delta
    previous = state.load_previous(out_path)
    version = state.next_version(previous)

    # 2-3. Calculo + montagem
    payload = build_payload(raw, now, version, source)
    compute_issues = payload.pop("_compute_issues", [])
    rows = raw["skus"]

    # 4. Validacao + delta
    payload["meta"]["validation"] = validate.validate(payload, rows, compute_issues)
    payload["meta"]["delta"] = validate.compute_delta(payload, previous)
    _fill_kpi_deltas(payload, previous)

    # 5. Gravacao
    write_json_atomic(payload, out_path)
    logger.info("data.json gravado: %s (v%d, validation=%s)",
                out_path, version, payload["meta"]["validation"]["status"])

    # 6. E-mail
    if args.dry_run or args.no_email:
        logger.info("E-mail SUPRIMIDO (%s).", "dry-run" if args.dry_run else "--no-email")
    else:
        notify.send_alert(payload)

    # 7. Publicacao
    if args.dry_run or args.no_publish:
        logger.info("Publicacao SUPRIMIDA (%s).",
                    "dry-run" if args.dry_run else "--no-publish")
    else:
        publish.publish(payload, out_path)

    status = payload["meta"]["validation"]["status"]
    logger.info("=== ETL S&OE fim | validation=%s ===", status)
    # exit code: 0 ok/warn, 2 error de validacao
    return 0 if status in ("ok", "warn") else 2


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ETL do painel S&OE GLB-GFM.")
    p.add_argument("--source", choices=["sql", "powerbi", "sqlite"], default=None,
                   help="Fonte de dados (default: SOE_SOURCE do .env ou 'sqlite').")
    p.add_argument("--dry-run", action="store_true",
                   help="Nao envia e-mail e nao publica (apenas grava data.json).")
    p.add_argument("--no-email", action="store_true", help="Nao envia e-mail.")
    p.add_argument("--no-publish", action="store_true", help="Nao faz git push.")
    p.add_argument("--out", default=str(config.DEFAULT_OUT),
                   help=f"Caminho do data.json (default: {config.DEFAULT_OUT}).")
    p.add_argument("--verbose", action="store_true", help="Log em nivel DEBUG.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    setup_logging(args.verbose)
    try:
        return run_pipeline(args)
    except Exception as exc:  # noqa: BLE001 - log fatal estruturado
        logger.exception("ETL FALHOU: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
