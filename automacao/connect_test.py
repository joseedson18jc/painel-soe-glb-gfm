#!/usr/bin/env python3
"""Ferramenta de DIAGNOSTICO e MAPEAMENTO da fonte real do ETL S&OE.

Conecta a fonte configurada em `SOE_SOURCE` (sql | powerbi), valida o contrato
de colunas (`EXPECTED_COLUMNS` de soe/sources.py) e ajuda o usuario a mapear o
esquema REAL para os 15 aliases que o ETL espera.

Modos (CLI argparse):
  (default)      Testa a conexao end-to-end e, se houver query/DAX, mostra
                 colunas + amostra e compara com o contrato.
  --introspect   Lista o esquema disponivel (tabelas/views + colunas/tipos).
  --map "a,b,c"  Recebe as colunas REAIS do usuario e gera, pronto-para-colar,
                 um SELECT SQL e um SELECTCOLUMNS DAX com os aliases do contrato
                 via match fuzzy (acentos/caixa/sinonimos pt-BR).

Caracteristicas:
  - Nucleo em stdlib. sqlalchemy/pyodbc/msal/requests sao importados de forma
    LAZY, com mensagem de "pip install X" se faltarem.
  - SOMENTE LEITURA: nao grava data.json, nao publica, nao altera dados.
  - Mensagens de erro acionaveis (driver ODBC, login, timeout, permissao).

Uso:
    python3 connect_test.py                 # teste de conexao
    python3 connect_test.py --introspect    # lista esquema
    python3 connect_test.py --introspect --schema dbo --table v_soe_skus
    python3 connect_test.py --map "Cod_SKU, Familia, Forecast_Sem, Estoque"
"""
from __future__ import annotations

import argparse
import sys
import time
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

# Reutiliza config (SETTINGS, parser .env, helper get) e o contrato de colunas.
from soe import config
from soe.sources import EXPECTED_COLUMNS, _strip_dax_keys

# --------------------------------------------------------------------------- #
# Saida colorida-leve (degrada para texto puro se nao for TTY)                 #
# --------------------------------------------------------------------------- #
_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    """Aplica codigo ANSI se a saida for um terminal."""
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def ok(msg: str) -> None:
    """Imprime uma linha de sucesso."""
    print(f"{_c('92', '[ OK ]')} {msg}")


def fail(msg: str) -> None:
    """Imprime uma linha de falha."""
    print(f"{_c('91', '[FALHA]')} {msg}")


def warn(msg: str) -> None:
    """Imprime um aviso."""
    print(f"{_c('93', '[AVISO]')} {msg}")


def info(msg: str) -> None:
    """Imprime uma linha informativa."""
    print(f"{_c('96', '[INFO]')} {msg}")


def header(title: str) -> None:
    """Imprime um cabecalho de secao."""
    print()
    print(_c("1;97", f"== {title} =="))


# --------------------------------------------------------------------------- #
# Helpers de apresentacao                                                      #
# --------------------------------------------------------------------------- #
def _print_table(columns: Sequence[str], rows: Sequence[Sequence[Any]], max_rows: int = 3) -> None:
    """Imprime uma tabela simples (colunas + ate `max_rows` linhas)."""
    cols = [str(c) for c in columns]
    sample = [[("" if v is None else str(v)) for v in r] for r in rows[:max_rows]]
    widths = [len(c) for c in cols]
    for r in sample:
        for i, v in enumerate(r):
            if i < len(widths):
                widths[i] = max(widths[i], min(len(v), 40))

    def _fmt(values: Sequence[str]) -> str:
        return " | ".join(v[:40].ljust(widths[i]) for i, v in enumerate(values) if i < len(widths))

    print("  " + _fmt(cols))
    print("  " + "-+-".join("-" * w for w in widths))
    for r in sample:
        print("  " + _fmt(r))


def _compare_contract(returned: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Compara colunas retornadas com EXPECTED_COLUMNS.

    Retorna (faltantes, extras). A comparacao e case-insensitive.
    """
    ret_lc = {c.lower() for c in returned}
    exp_lc = {c.lower() for c in EXPECTED_COLUMNS}
    missing = [c for c in EXPECTED_COLUMNS if c.lower() not in ret_lc]
    extras = [c for c in returned if c.lower() not in exp_lc]
    return missing, extras


def _report_contract(returned: Sequence[str]) -> bool:
    """Reporta conformidade com o contrato. Retorna True se 100% conforme."""
    missing, extras = _compare_contract(returned)
    header("CONTRATO (EXPECTED_COLUMNS)")
    info(f"Colunas retornadas ({len(returned)}): {', '.join(returned)}")
    if not missing:
        ok("Todos os 15 aliases do contrato estao presentes.")
    else:
        fail(f"Faltam {len(missing)} alias(es) do contrato: {', '.join(missing)}")
        info("Use 'python3 connect_test.py --map \"<suas_colunas>\"' p/ gerar o SELECT/DAX.")
    if extras:
        warn(f"Colunas extras (serao ignoradas pelo ETL): {', '.join(extras)}")
    return not missing


# --------------------------------------------------------------------------- #
# Mapeamento de erros -> acoes                                                 #
# --------------------------------------------------------------------------- #
def diagnose_error(exc: BaseException) -> None:
    """Imprime um diagnostico acionavel a partir de uma excecao de conexao."""
    msg = str(exc).lower()
    fail(f"{type(exc).__name__}: {exc}")

    if any(k in msg for k in ("odbc driver", "im002", "data source name", "libodbc", "can't open lib")):
        warn("Driver ODBC ausente/mal configurado.")
        info("  macOS:  brew install msodbcsql18 mssql-tools18")
        info("  E use na URL: ...?driver=ODBC+Driver+18+for+SQL+Server")
    elif any(k in msg for k in ("login failed", "28000", "authentication", "password", "18456", "aadsts")):
        warn("Falha de autenticacao.")
        info("  Cheque usuario/senha; se houver MFA/2FA use service principal ou token.")
        info("  Confirme que o login tem acesso ao banco/dataset alvo.")
    elif any(k in msg for k in ("timeout", "timed out", "connection refused", "10060",
                                "could not connect", "no route", "08001", "getaddrinfo")):
        warn("Conexao recusada / timeout.")
        info("  Verifique host/porta, firewall, VPN e se o servico esta no ar.")
        info("  SQL Server padrao = TCP 1433; confirme allowlist de IP.")
    elif any(k in msg for k in ("forbidden", "unauthorized", "401", "403",
                                "powerbinotauthorized", "principal")):
        warn("Sem permissao no recurso (Power BI dataset ou DB).")
        info("  Power BI: de acesso ao Service Principal no workspace (Member/Contributor)")
        info("  e habilite 'Service principals can use Power BI APIs' no Admin Portal.")
    elif "404" in msg or "datasetnotfound" in msg:
        warn("Dataset/recurso nao encontrado.")
        info("  Confirme PBI_DATASET_ID (e PBI_GROUP_ID se aplicavel).")
    else:
        info("  Sem diagnostico especifico. Releia a mensagem acima e cheque rede/credenciais.")


# --------------------------------------------------------------------------- #
# Imports LAZY com mensagem clara                                             #
# --------------------------------------------------------------------------- #
def _import_sqlalchemy():
    """Importa SQLAlchemy (create_engine, text) de forma lazy."""
    try:
        from sqlalchemy import create_engine, text  # type: ignore
        return create_engine, text
    except ImportError as exc:  # pragma: no cover
        fail("SQLAlchemy nao instalado.")
        info("  pip install sqlalchemy pyodbc   (pyodbc p/ SQL Server)")
        raise SystemExit(2) from exc


def _import_powerbi():
    """Importa msal e requests de forma lazy."""
    try:
        import msal  # type: ignore
        import requests  # type: ignore
        return msal, requests
    except ImportError as exc:  # pragma: no cover
        fail("Dependencias de Power BI ausentes.")
        info("  pip install msal requests")
        raise SystemExit(2) from exc


# --------------------------------------------------------------------------- #
# Backend SQL                                                                  #
# --------------------------------------------------------------------------- #
def _sql_engine():
    """Cria um engine SQLAlchemy a partir de SOE_SQL_URL."""
    s = config.SETTINGS
    if not s.sql_url:
        fail("SOE_SQL_URL nao definido no .env (obrigatorio para source='sql').")
        raise SystemExit(2)
    create_engine, _ = _import_sqlalchemy()
    return create_engine(s.sql_url, pool_pre_ping=True), s


def _limited_query(raw_query: str, n: int = 3) -> str:
    """Embrulha a query do usuario para retornar poucas linhas, de forma portavel.

    Usa uma subquery com LIMIT (ANSI/Postgres/MySQL/SQLite). Se o backend nao
    aceitar (ex.: SQL Server, que usa TOP), o chamador faz fallback p/ a query
    original; aqui apenas inspecionamos as colunas.
    """
    q = raw_query.strip().rstrip(";")
    return f"SELECT * FROM (\n{q}\n) AS _soe_probe LIMIT {n}"


def test_sql(args: argparse.Namespace) -> bool:
    """Teste de conexao SQL: SELECT 1, latencia e (se houver) amostra da query."""
    header("TESTE DE CONEXAO - SQL")
    try:
        engine, s = _sql_engine()
    except SystemExit:
        return False

    _, text = _import_sqlalchemy()
    info(f"URL: {_redact_url(s.sql_url)}")

    # 1) SELECT 1 + latencia
    try:
        t0 = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        dt_ms = (time.perf_counter() - t0) * 1000.0
        ok(f"Conexao estabelecida (SELECT 1) em {dt_ms:.0f} ms.")
    except Exception as exc:  # noqa: BLE001 - queremos diagnosticar tudo
        diagnose_error(exc)
        return False

    # 2) Query do contrato (opcional)
    if not s.sql_query:
        warn("SOE_SQL_QUERY nao definido: pulei a verificacao do contrato.")
        info("  Defina SOE_SQL_QUERY no .env ou use --map p/ montar o SELECT.")
        return True

    header("AMOSTRA DA QUERY (SOE_SQL_QUERY)")
    for probe in (_limited_query(s.sql_query, 3), s.sql_query):
        try:
            with engine.connect() as conn:
                result = conn.execute(text(probe))
                cols = list(result.keys())
                rows = result.fetchmany(3)
            ok(f"Query executada. {len(cols)} colunas, mostrando ate 3 linhas.")
            _print_table(cols, [list(r) for r in rows])
            _report_contract(cols)
            return True
        except Exception as exc:  # noqa: BLE001
            # primeira tentativa pode falhar se o backend nao aceitar a subquery LIMIT
            if probe != s.sql_query:
                warn("Probe com LIMIT falhou; tentando a query original (pode trazer muitas linhas).")
                continue
            diagnose_error(exc)
            return False
    return False


def introspect_sql(args: argparse.Namespace) -> bool:
    """Lista tabelas/views e colunas via information_schema."""
    header("INTROSPECCAO - SQL (information_schema)")
    try:
        engine, _ = _sql_engine()
    except SystemExit:
        return False
    _, text = _import_sqlalchemy()

    where: List[str] = []
    params: Dict[str, Any] = {}
    if args.schema:
        where.append("table_schema = :schema")
        params["schema"] = args.schema
    if args.table:
        where.append("table_name = :table")
        params["table"] = args.table
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    tables_sql = (
        "SELECT table_schema, table_name, table_type "
        "FROM information_schema.tables" + clause +
        " ORDER BY table_schema, table_name"
    )
    cols_sql = (
        "SELECT table_schema, table_name, column_name, data_type "
        "FROM information_schema.columns" + clause +
        " ORDER BY table_schema, table_name, ordinal_position"
    )
    try:
        with engine.connect() as conn:
            tables = list(conn.execute(text(tables_sql), params))
            columns = list(conn.execute(text(cols_sql), params))
    except Exception as exc:  # noqa: BLE001
        diagnose_error(exc)
        return False

    if not tables:
        warn("Nenhuma tabela/view encontrada com os filtros informados.")
        return True

    ok(f"{len(tables)} tabela(s)/view(s) encontradas.")
    by_table: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for sch, tbl, col, dtype in columns:
        by_table.setdefault((str(sch), str(tbl)), []).append((str(col), str(dtype)))

    for sch, tbl, ttype in tables:
        kind = "VIEW" if str(ttype).upper().startswith("VIEW") else "TABLE"
        print()
        print(f"  {_c('1;97', f'{sch}.{tbl}')}  ({kind})")
        for col, dtype in by_table.get((str(sch), str(tbl)), []):
            print(f"      - {col}  : {dtype}")
    info("Copie as colunas relevantes e rode: "
         "python3 connect_test.py --map \"col1, col2, ...\"")
    return True


# --------------------------------------------------------------------------- #
# Backend Power BI                                                             #
# --------------------------------------------------------------------------- #
def _pbi_token() -> Tuple[str, Any]:
    """Obtem o access_token via client credentials (msal). Retorna (token, requests)."""
    s = config.SETTINGS
    missing = [
        n for n, v in {
            "PBI_TENANT_ID": s.pbi_tenant,
            "PBI_CLIENT_ID": s.pbi_client_id,
            "PBI_CLIENT_SECRET": s.pbi_client_secret,
            "PBI_DATASET_ID": s.pbi_dataset_id,
        }.items() if not v
    ]
    if missing:
        fail(f"Variaveis Power BI ausentes no .env: {', '.join(missing)}")
        raise SystemExit(2)

    msal, requests = _import_powerbi()
    authority = f"https://login.microsoftonline.com/{s.pbi_tenant}"
    app = msal.ConfidentialClientApplication(
        s.pbi_client_id, authority=authority, client_credential=s.pbi_client_secret
    )
    token = app.acquire_token_for_client(
        scopes=["https://analysis.windows.net/powerbi/api/.default"]
    )
    if "access_token" not in token:
        fail(f"Falha ao obter token: {token.get('error')} - {token.get('error_description')}")
        info("  Cheque PBI_TENANT_ID / PBI_CLIENT_ID / PBI_CLIENT_SECRET (service principal).")
        raise SystemExit(2)
    return token["access_token"], requests


def _pbi_execute_dax(access_token: str, requests: Any, dax: str) -> Tuple[List[str], List[List[Any]]]:
    """Executa um DAX via REST executeQueries. Retorna (colunas, linhas)."""
    s = config.SETTINGS
    url = f"https://api.powerbi.com/v1.0/myorg/datasets/{s.pbi_dataset_id}/executeQueries"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    rows_raw = resp.json()["results"][0]["tables"][0]["rows"]
    rows = [_strip_dax_keys(r) for r in rows_raw]
    cols = list(rows[0].keys()) if rows else []
    table = [[r.get(c) for c in cols] for r in rows]
    return cols, table


def test_powerbi(args: argparse.Namespace) -> bool:
    """Teste de conexao Power BI: token + DAX trivial + (se houver) DAX do contrato."""
    header("TESTE DE CONEXAO - POWER BI")
    try:
        token, requests = _pbi_token()
    except SystemExit:
        return False
    ok("Token obtido (client credentials / service principal).")

    # DAX trivial p/ validar permissao no dataset
    try:
        t0 = time.perf_counter()
        cols, rows = _pbi_execute_dax(token, requests, 'EVALUATE ROW("ok", 1)')
        dt_ms = (time.perf_counter() - t0) * 1000.0
        ok(f"executeQueries respondeu em {dt_ms:.0f} ms. {cols} = {rows}")
    except Exception as exc:  # noqa: BLE001
        diagnose_error(exc)
        return False

    s = config.SETTINGS
    if not s.pbi_dax:
        warn("PBI_DAX_QUERY nao definido: pulei a verificacao do contrato.")
        info("  Defina PBI_DAX_QUERY no .env ou use --map p/ montar o SELECTCOLUMNS.")
        return True

    header("AMOSTRA DA QUERY (PBI_DAX_QUERY)")
    try:
        cols, rows = _pbi_execute_dax(token, requests, s.pbi_dax)
        ok(f"DAX executado. {len(cols)} colunas, mostrando ate 3 linhas.")
        _print_table(cols, rows)
        _report_contract(cols)
        return True
    except Exception as exc:  # noqa: BLE001
        diagnose_error(exc)
        return False


def introspect_powerbi(args: argparse.Namespace) -> bool:
    """Lista esquema via DMV (INFO.TABLES / INFO.COLUMNS); orienta fallback manual."""
    header("INTROSPECCAO - POWER BI (DMV)")
    try:
        token, requests = _pbi_token()
    except SystemExit:
        return False

    found_any = False
    for label, dax in (
        ("TABELAS", "EVALUATE INFO.TABLES()"),
        ("COLUNAS", "EVALUATE INFO.COLUMNS()"),
    ):
        try:
            cols, rows = _pbi_execute_dax(token, requests, dax)
            ok(f"{label}: {len(rows)} linha(s).")
            _print_table(cols, rows, max_rows=30)
            found_any = True
        except Exception as exc:  # noqa: BLE001
            warn(f"{label}: DMV indisponivel ({type(exc).__name__}).")

    if not found_any:
        info("DMV INFO.* indisponivel neste dataset.")
        info("  Abra o dataset no Power BI, copie os nomes das colunas e rode:")
        info("  python3 connect_test.py --map \"Tabela_Coluna1, Tabela_Coluna2, ...\"")
    return True


# --------------------------------------------------------------------------- #
# Modo --map: match fuzzy de colunas reais -> aliases do contrato             #
# --------------------------------------------------------------------------- #
def _normalize(s: str) -> str:
    """Remove acentos, baixa caixa e troca separadores por '_' (sem repetir '_')."""
    nfkd = unicodedata.normalize("NFKD", s)
    no_accent = "".join(c for c in nfkd if not unicodedata.combining(c))
    chars = [ch if ch.isalnum() else "_" for ch in no_accent.lower()]
    return "_".join(filter(None, "".join(chars).split("_")))


# Sinonimos/pistas pt-BR -> alias do contrato. Cada alias mapeia para uma lista
# de tokens; se algum token aparecer (como substring normalizada) ha candidato.
_SYNONYMS: Dict[str, List[str]] = {
    "sku":               ["sku", "cod_sku", "codigo", "item", "material", "produto"],
    "familia":           ["familia", "family", "grupo", "linha_produto", "categoria"],
    "bitola":            ["bitola", "gauge", "diametro", "espessura", "secao"],
    "demanda_sem_t":     ["demanda_sem", "demanda_semana", "demanda", "forecast", "previsao_sem"],
    "sigma_sem_t":       ["sigma", "desvio", "desvio_padrao", "std", "stddev", "variabilidade"],
    "plano_t":           ["plano", "planejado", "plan", "plano_producao", "ppcp"],
    "estoque_t":         ["estoque", "inventory", "inventario", "saldo", "estoque_atual"],
    "producao_real_t":   ["producao_real", "producao", "realizado", "produzido", "output_real"],
    "demanda_prev_t":    ["demanda_prev", "previsao", "forecast", "prev", "demanda_prevista"],
    "demanda_real_t":    ["demanda_real", "real", "realizado", "vendas", "demanda_realizada"],
    "otif_pct":          ["otif", "on_time", "nivel_servico", "service_level", "atendimento"],
    "lead_time_dias":    ["lead_time", "leadtime", "lt", "prazo", "lead"],
    "preco_rs_t":        ["preco", "price", "preco_venda", "valor_unitario", "preco_rs"],
    "ebitda_rs_t":       ["ebitda", "margem", "margin", "margem_ebitda", "contribuicao"],
    "custo_estoque_rs_t": ["custo_estoque", "carrying", "custo_carregamento", "holding", "custo_inventario"],
}

# Ordem por especificidade: aliases mais especificos primeiro (evita que um
# 'demanda' generico roube o match de 'demanda_prev_t').
_ALIAS_ORDER = [
    "demanda_sem_t", "demanda_prev_t", "demanda_real_t",
    "custo_estoque_rs_t", "plano_t", "producao_real_t", "lead_time_dias",
    "otif_pct", "sigma_sem_t", "estoque_t",
    "preco_rs_t", "ebitda_rs_t", "sku", "familia", "bitola",
]


def _score(real_norm: str, alias: str) -> int:
    """Pontua o quao bem `real_norm` casa com `alias` (0 = sem match)."""
    best = 0
    alias_norm = _normalize(alias)
    for token in _SYNONYMS.get(alias, []):
        tnorm = _normalize(token)
        if real_norm == tnorm or real_norm == alias_norm:
            best = max(best, 100)  # match exato/forte
        elif real_norm.startswith(tnorm) or real_norm.endswith(tnorm):
            best = max(best, 80)
        elif tnorm in real_norm:
            best = max(best, 60 + min(len(tnorm), 20))
    return best


def map_columns(real_columns: List[str]) -> Dict[str, Optional[str]]:
    """Casa cada alias do contrato com a melhor coluna real (ou None).

    Estrategia: para cada alias (na ordem de especificidade), escolhe a coluna
    real de maior score ainda nao consumida. Aliases sem match ficam None.
    """
    reals = [(c.strip(), _normalize(c)) for c in real_columns if c.strip()]
    mapping: Dict[str, Optional[str]] = {a: None for a in EXPECTED_COLUMNS}
    used: Set[str] = set()

    for alias in _ALIAS_ORDER:
        best_col: Optional[str] = None
        best_score = 0
        for real, real_norm in reals:
            if real in used:
                continue
            sc = _score(real_norm, alias)
            if sc > best_score:
                best_score, best_col = sc, real
        if best_col is not None and best_score >= 60:
            mapping[alias] = best_col
            used.add(best_col)
    return mapping


def emit_mapping(real_columns: List[str]) -> bool:
    """Imprime o mapeamento + SELECT SQL e SELECTCOLUMNS DAX prontos p/ colar."""
    header("MAPEAMENTO FUZZY (colunas reais -> contrato)")
    mapping = map_columns(real_columns)

    matched = {a: c for a, c in mapping.items() if c}
    missing = [a for a, c in mapping.items() if not c]

    for alias in EXPECTED_COLUMNS:
        real = mapping[alias]
        if real:
            ok(f"{alias:<20} <= {real}")
        else:
            warn(f"{alias:<20} <= (SEM MATCH - preencha manualmente)")

    info(f"{len(matched)}/{len(EXPECTED_COLUMNS)} aliases mapeados automaticamente.")
    if missing:
        warn(f"Preencha manualmente: {', '.join(missing)}")

    # (a) SELECT SQL
    header("SQL pronto-para-colar (SOE_SQL_QUERY)")
    # Coloca a virgula ANTES do comentario inline para nao "comer" o separador.
    last = len(EXPECTED_COLUMNS) - 1
    print("SELECT")
    for i, alias in enumerate(EXPECTED_COLUMNS):
        real = mapping[alias]
        comma = "" if i == last else ","
        if real:
            print(f"    {real} AS {alias}{comma}")
        else:
            print(f"    NULL AS {alias}{comma}  -- TODO: mapear coluna real")
    print("FROM <sua_tabela_ou_view>;")

    # (b) SELECTCOLUMNS DAX
    header("DAX pronto-para-colar (PBI_DAX_QUERY)")
    print("EVALUATE")
    print("SELECTCOLUMNS(")
    print("    Tabela,")
    for i, alias in enumerate(EXPECTED_COLUMNS):
        real = mapping[alias]
        comma = "" if i == last else ","
        if real:
            print(f'    "{alias}", Tabela[{real}]{comma}')
        else:
            print(f'    "{alias}", BLANK(){comma}  -- TODO: mapear coluna real')
    print(")")
    info("Troque 'Tabela' / '<sua_tabela_ou_view>' pelos nomes reais do seu modelo.")
    return not missing


# --------------------------------------------------------------------------- #
# Utilitarios                                                                  #
# --------------------------------------------------------------------------- #
def _redact_url(url: Optional[str]) -> str:
    """Oculta a senha numa URL de conexao (driver://user:pass@host/db)."""
    if not url:
        return "(vazio)"
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, _, host = rest.partition("@")
        if ":" in creds:
            user = creds.split(":", 1)[0]
            return f"{scheme}://{user}:***@{host}"
    return url


def _print_summary(source: str, success: bool, mode: str) -> None:
    """Imprime o RESUMO final com proximos passos."""
    header("RESUMO")
    if success:
        ok(f"Modo '{mode}' concluido para source='{source}'.")
    else:
        fail(f"Modo '{mode}' encontrou problemas para source='{source}'.")

    print()
    print(_c("1;97", "Proximos passos:"))
    if mode == "test" and success:
        print("  1. Se o contrato acusou faltantes, rode --map com suas colunas reais.")
        print("  2. Ajuste SOE_SQL_QUERY / PBI_DAX_QUERY no .env com o SELECT/DAX gerado.")
        print("  3. Rode o ETL real: python3 soe_etl.py (ou via run.sh).")
    elif mode == "introspect":
        print("  1. Identifique a tabela/view com os dados de SKU.")
        print("  2. Rode: python3 connect_test.py --map \"<colunas_da_view>\"")
        print("  3. Cole o SELECT/DAX gerado em SOE_SQL_QUERY / PBI_DAX_QUERY no .env.")
    elif mode == "map":
        print("  1. Revise os aliases SEM MATCH e preencha-os no SELECT/DAX.")
        print("  2. Cole em SOE_SQL_QUERY (sql) ou PBI_DAX_QUERY (powerbi) no .env.")
        print("  3. Valide com: python3 connect_test.py")
    else:
        print("  - Resolva os erros apontados acima e rode novamente.")
        print("  - Detalhes de configuracao em SETUP.md / .env.example.")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    """Constroi o parser de argumentos."""
    p = argparse.ArgumentParser(
        prog="connect_test.py",
        description="Diagnostico e mapeamento da fonte real do ETL S&OE (somente leitura).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--introspect", action="store_true",
        help="Lista o esquema disponivel (tabelas/views + colunas).",
    )
    g.add_argument(
        "--map", dest="map_cols", metavar='"col1,col2,..."',
        help="Gera SELECT SQL e SELECTCOLUMNS DAX mapeando colunas reais -> contrato.",
    )
    p.add_argument("--schema", help="(introspect/sql) Filtra por schema.")
    p.add_argument("--table", help="(introspect/sql) Filtra por tabela/view.")
    p.add_argument(
        "--source", choices=["sql", "powerbi"],
        help="Forca a fonte (sobrepoe SOE_SOURCE do .env).",
    )
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Ponto de entrada. Retorna codigo de saida (0 = ok)."""
    args = build_parser().parse_args(argv)

    # Modo --map nao precisa de conexao.
    if args.map_cols is not None:
        real_cols = list(args.map_cols.replace(";", ",").split(","))
        success = emit_mapping(real_cols)
        _print_summary(config.SETTINGS.source, success, "map")
        return 0 if success else 1

    source = (args.source or config.SETTINGS.source or "").strip().lower()
    info(f"SOE_SOURCE resolvido: {source!r}")
    if source in ("sqlite", "fixture"):
        warn("source atual e o fixture local (SQLite). Nao ha conexao remota p/ testar.")
        info("  Defina SOE_SOURCE=sql ou powerbi no .env, ou use --source sql|powerbi.")
        _print_summary(source, True, "test")
        return 0
    if source not in ("sql", "powerbi"):
        fail(f"source desconhecida: {source!r}. Use 'sql' ou 'powerbi'.")
        return 2

    mode = "introspect" if args.introspect else "test"
    if source == "sql":
        success = introspect_sql(args) if args.introspect else test_sql(args)
    else:
        success = introspect_powerbi(args) if args.introspect else test_powerbi(args)

    _print_summary(source, success, mode)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
