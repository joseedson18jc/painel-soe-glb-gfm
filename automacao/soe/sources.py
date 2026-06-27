"""Adaptadores de fonte de dados para o ETL S&OE.

Todos os adaptadores retornam a MESMA estrutura:

    RawData = {
        "skus":  List[Dict[str, Any]],   # uma linha por SKU
        "linhas": List[Dict[str, Any]],  # capacidades de linha
        "meta":  Dict[str, Any],         # metadados (ns_meta, lead_time padrao...)
    }

Colunas/aliases esperados por SKU (EXPECTED_COLUMNS). O SELECT de uma fonte SQL
deve produzir EXATAMENTE estes aliases:

    SELECT
        sku                 AS sku,
        familia             AS familia,
        bitola              AS bitola,
        demanda_sem_t       AS demanda_sem_t,        -- demanda da semana (t)
        sigma_sem_t         AS sigma_sem_t,          -- desvio-padrao semanal (t) [pode ser NULL]
        plano_t             AS plano_t,              -- plano de producao (t)
        estoque_t           AS estoque_t,            -- estoque atual (t)
        producao_real_t     AS producao_real_t,      -- producao realizada (t)
        demanda_prev_t      AS demanda_prev_t,       -- previsao (t) p/ MAPE/bias
        demanda_real_t      AS demanda_real_t,       -- realizado (t) p/ MAPE/bias
        otif_pct            AS otif_pct,             -- OTIF do SKU (%)
        lead_time_dias      AS lead_time_dias,       -- lead time (dias)
        preco_rs_t          AS preco_rs_t,           -- preco de venda (R$/t)
        ebitda_rs_t         AS ebitda_rs_t,          -- margem EBITDA (R$/t)
        custo_estoque_rs_t  AS custo_estoque_rs_t    -- custo de carregar estoque (R$/t)
    FROM v_soe_skus;

Para Power BI, a query DAX deve devolver colunas com os mesmos nomes (o prefixo
de tabela e removido automaticamente).

Apenas o adaptador "sqlite" usa stdlib pura. "sql" e "powerbi" importam suas
dependencias (sqlalchemy/pyodbc, requests/msal) de forma LAZY.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List

from . import config

logger = logging.getLogger("soe.sources")

EXPECTED_COLUMNS = [
    "sku", "familia", "bitola", "demanda_sem_t", "sigma_sem_t", "plano_t",
    "estoque_t", "producao_real_t", "demanda_prev_t", "demanda_real_t",
    "otif_pct", "lead_time_dias", "preco_rs_t", "ebitda_rs_t", "custo_estoque_rs_t",
]
_NUMERIC = {c for c in EXPECTED_COLUMNS if c not in ("sku", "familia", "bitola")}

RawData = Dict[str, Any]


def _coerce_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza tipos: strings de texto preservadas, numericos -> float|None."""
    out: Dict[str, Any] = {}
    for col in EXPECTED_COLUMNS:
        val = row.get(col)
        if col in ("sku", "familia", "bitola"):
            out[col] = str(val).strip() if val is not None else ""
        else:
            if val is None or val == "":
                out[col] = None
            else:
                try:
                    out[col] = float(val)
                except (TypeError, ValueError):
                    out[col] = None
    return out


# --------------------------------------------------------------------------- #
# SQLite (stdlib)                                                              #
# --------------------------------------------------------------------------- #
def _from_sqlite() -> RawData:
    db = config.FIXTURE_DB
    if not db.exists():
        raise FileNotFoundError(
            f"Fixture SQLite nao encontrado: {db}. "
            f"Rode 'python3 fixtures/seed_sqlite.py' primeiro."
        )
    conn = sqlite3.connect(str(db))
    try:
        conn.row_factory = sqlite3.Row
        skus = [_coerce_row(dict(r)) for r in conn.execute("SELECT * FROM skus")]
        linhas = [
            {
                "linha": str(r["linha"]),
                "capacidade_t": float(r["capacidade_t"]),
                "utilizado_t": float(r["utilizado_t"]),
            }
            for r in conn.execute("SELECT * FROM linhas")
        ]
        meta = {r["chave"]: r["valor"] for r in conn.execute("SELECT * FROM metadados")}
    finally:
        conn.close()
    logger.info("sqlite: %d SKUs, %d linhas", len(skus), len(linhas))
    return {"skus": skus, "linhas": linhas, "meta": meta}


# --------------------------------------------------------------------------- #
# SQL (SQLAlchemy/pyodbc, LAZY)                                                #
# --------------------------------------------------------------------------- #
def _from_sql() -> RawData:
    s = config.SETTINGS
    if not s.sql_url or not s.sql_query:
        raise ValueError("SOE_SQL_URL e SOE_SQL_QUERY sao obrigatorios para source='sql'.")
    try:
        from sqlalchemy import create_engine, text  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "source='sql' requer SQLAlchemy instalado (pip install sqlalchemy pyodbc)."
        ) from exc

    engine = create_engine(s.sql_url)
    with engine.connect() as conn:
        result = conn.execute(text(s.sql_query))
        cols = list(result.keys())
        skus = [_coerce_row(dict(zip(cols, r))) for r in result.fetchall()]
    # linhas/meta opcionais via .env (JSON); senao derivamos no compute.
    linhas = _linhas_from_env()
    meta = {"ns_meta_pct": str(s.ns_meta)}
    logger.info("sql: %d SKUs", len(skus))
    return {"skus": skus, "linhas": linhas, "meta": meta}


# --------------------------------------------------------------------------- #
# Power BI (REST executeQueries / DAX, LAZY)                                   #
# --------------------------------------------------------------------------- #
def _from_powerbi() -> RawData:
    s = config.SETTINGS
    missing = [
        n for n, v in {
            "PBI_TENANT_ID": s.pbi_tenant,
            "PBI_CLIENT_ID": s.pbi_client_id,
            "PBI_CLIENT_SECRET": s.pbi_client_secret,
            "PBI_DATASET_ID": s.pbi_dataset_id,
            "PBI_DAX_QUERY": s.pbi_dax,
        }.items() if not v
    ]
    if missing:
        raise ValueError(f"source='powerbi' requer .env: {', '.join(missing)}")
    try:
        import msal  # type: ignore
        import requests  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "source='powerbi' requer 'msal' e 'requests' (pip install msal requests)."
        ) from exc

    authority = f"https://login.microsoftonline.com/{s.pbi_tenant}"
    app = msal.ConfidentialClientApplication(
        s.pbi_client_id, authority=authority, client_credential=s.pbi_client_secret
    )
    token = app.acquire_token_for_client(
        scopes=["https://analysis.windows.net/powerbi/api/.default"]
    )
    if "access_token" not in token:
        raise RuntimeError(f"Falha ao obter token Power BI: {token.get('error_description')}")

    url = f"https://api.powerbi.com/v1.0/myorg/datasets/{s.pbi_dataset_id}/executeQueries"
    headers = {
        "Authorization": f"Bearer {token['access_token']}",
        "Content-Type": "application/json",
    }
    payload = {"queries": [{"query": s.pbi_dax}], "serializerSettings": {"includeNulls": True}}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    rows = resp.json()["results"][0]["tables"][0]["rows"]
    # DAX devolve chaves no formato "Tabela[coluna]"; mapeamos p/ coluna nua.
    skus = [_coerce_row(_strip_dax_keys(r)) for r in rows]
    linhas = _linhas_from_env()
    meta = {"ns_meta_pct": str(s.ns_meta)}
    logger.info("powerbi: %d SKUs", len(skus))
    return {"skus": skus, "linhas": linhas, "meta": meta}


def _strip_dax_keys(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        nk = k.split("[")[-1].rstrip("]") if "[" in k else k
        out[nk] = v
    return out


def _linhas_from_env() -> List[Dict[str, Any]]:
    """Le capacidades de linha de SOE_LINHAS_JSON (opcional). Vazio se ausente."""
    import json

    raw = config.get("SOE_LINHAS_JSON")
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [
            {
                "linha": str(d["linha"]),
                "capacidade_t": float(d["capacidade_t"]),
                "utilizado_t": float(d["utilizado_t"]),
            }
            for d in data
        ]
    except Exception as exc:  # pragma: no cover
        logger.warning("SOE_LINHAS_JSON invalido: %s", exc)
        return []


# --------------------------------------------------------------------------- #
# Dispatcher                                                                   #
# --------------------------------------------------------------------------- #
_ADAPTERS = {
    "sqlite": _from_sqlite,
    "fixture": _from_sqlite,  # alias: 'fixture' == 'sqlite' (modo demo, SQLite local)
    "sql": _from_sql,
    "powerbi": _from_powerbi,
}


def get_dataframe(source: str) -> RawData:
    """Retorna RawData para a fonte pedida. Levanta ValueError se desconhecida."""
    key = (source or "").strip().lower()
    if key not in _ADAPTERS:
        raise ValueError(f"source desconhecida: {source!r}. Use {list(_ADAPTERS)}.")
    data = _ADAPTERS[key]()
    if not data.get("skus"):
        raise RuntimeError(f"source '{key}' retornou 0 SKUs.")
    return data
