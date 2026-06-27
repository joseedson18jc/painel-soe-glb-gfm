#!/usr/bin/env bash
#
# setup_source.sh - Prepara o ambiente Python para a FONTE REAL do ETL S&OE
#                   (painel GLB-GFM).
#
# O nucleo do soe_etl.py roda so com a stdlib (modo sqlite/fixture, demo). Para
# apontar para a fonte de PRODUCAO (SQL Server, Postgres ou Power BI) e preciso
# instalar os drivers do adaptador correspondente. Este script faz isso de forma
# IDEMPOTENTE: pode ser rodado quantas vezes quiser sem efeitos colaterais.
#
#   Fluxo: cria/ativa venv -> carrega .env (descobre SOE_SOURCE) -> instala os
#          drivers do caso -> valida com connect_test.py --introspect.
#
# NAO mexe no launchd (use run.sh / launchd/ para producao agendada).
# Uso:  bash setup_source.sh
#
set -euo pipefail

# --- Caminhos absolutos (espelham run.sh; NAO dependem do cwd) ---------------
AUTOMACAO_DIR="/Users/jose.costa/Desktop/painel-soe-glb-gfm/automacao"
VENV_DIR="${AUTOMACAO_DIR}/.venv"
ENV_FILE="${AUTOMACAO_DIR}/.env"
CONNECT_TEST="${AUTOMACAO_DIR}/connect_test.py"

# --- Mensagens claras (stderr p/ avisos, stdout p/ progresso) ----------------
info()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[1;31m[erro]\033[0m %s\n' "$*" >&2; exit 1; }

info "================ INICIO setup_source.sh ================"

# --- 1) virtualenv: cria se nao existir, depois ativa ------------------------
if [[ -d "${VENV_DIR}" && -f "${VENV_DIR}/bin/activate" ]]; then
    info "venv ja existe em ${VENV_DIR} (reaproveitando)."
else
    info "Criando venv em ${VENV_DIR} ..."
    python3 -m venv "${VENV_DIR}" || die "Falha ao criar venv (python3 -m venv)."
    ok "venv criado."
fi

# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate" || die "Falha ao ativar venv ${VENV_DIR}."
PIP="python -m pip"
info "venv ativo: $(command -v python) ($(python --version 2>&1))"

# pip atualizado torna a instalacao de wheels (pyodbc/psycopg2/numpy) confiavel.
info "Atualizando pip (idempotente) ..."
${PIP} install --quiet --upgrade pip || warn "Nao consegui atualizar o pip; seguindo mesmo assim."

# --- 2) Carrega .env para descobrir SOE_SOURCE -------------------------------
# ATENCAO (mesma pegadinha de run.sh): no .env nao use comentario inline na
# mesma linha de um valor (VAR=valor # comentario) -> o '#...' entra no valor.
if [[ -f "${ENV_FILE}" ]]; then
    info "Carregando ${ENV_FILE} ..."
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
else
    warn "${ENV_FILE} nao encontrado (veja .env.example). Assumindo modo demo."
fi

# Default seguro: sqlite (demo, stdlib pura) - igual ao default do run.sh.
SOURCE="${SOE_SOURCE:-sqlite}"
info "SOE_SOURCE = ${SOURCE}"

# --- 3) Instala os drivers do caso -------------------------------------------
# numpy e SEMPRE tentado: acelera o Monte Carlo (sem ele cai p/ Python puro).
info "Instalando numpy (acelera o Monte Carlo; opcional) ..."
${PIP} install --quiet "numpy>=1.26" || warn "numpy nao instalado; Monte Carlo seguira em Python puro (mais lento)."

case "${SOURCE}" in
    sql)
        info "Fonte SQL (SQL Server / pyodbc): instalando SQLAlchemy + pyodbc ..."
        ${PIP} install --quiet "SQLAlchemy>=2.0" "pyodbc>=5.1" \
            || die "Falha ao instalar SQLAlchemy/pyodbc."

        # pyodbc precisa do driver ODBC do SO (msodbcsql18). Sem ele, conexao falha.
        # Detecta via odbcinst (pacote unixodbc) e instrui o brew se faltar.
        if command -v odbcinst >/dev/null 2>&1 \
           && odbcinst -q -d 2>/dev/null | grep -qiE 'ODBC Driver 1[78] for SQL Server'; then
            ok "Driver ODBC do SQL Server encontrado."
        else
            warn "Driver ODBC do SQL Server NAO encontrado."
            warn "Instale o driver no macOS com:"
            warn "    brew tap microsoft/mssql-release https://github.com/microsoft/homebrew-mssql-release"
            warn "    brew update"
            warn "    brew install msodbcsql18 unixodbc"
            warn "(sem isso, o pyodbc nao conecta no SQL Server)."
        fi
        ;;
    postgres|postgresql)
        info "Fonte Postgres: instalando SQLAlchemy + psycopg2-binary ..."
        ${PIP} install --quiet "SQLAlchemy>=2.0" "psycopg2-binary>=2.9" \
            || die "Falha ao instalar SQLAlchemy/psycopg2-binary."
        ok "Drivers Postgres prontos."
        ;;
    powerbi)
        info "Fonte Power BI: instalando msal + requests ..."
        ${PIP} install --quiet "msal>=1.28" "requests>=2.31" \
            || die "Falha ao instalar msal/requests."
        ok "Drivers Power BI prontos."
        ;;
    sqlite|fixture)
        info "Fonte sqlite/fixture (modo demo): so a stdlib e necessaria; nenhum driver extra."
        ;;
    *)
        warn "SOE_SOURCE='${SOURCE}' desconhecido (esperado: sql|postgres|powerbi|sqlite|fixture)."
        warn "Nenhum driver especifico instalado. Ajuste o .env e rode de novo."
        ;;
esac

# --- 4) Validacao final: connect_test.py --introspect ------------------------
# Roda a checagem de conexao/introspeccao da fonte real. Aceita --introspect e,
# como fallback, --test (nomes do contrato do caso). Se o script ainda nao existe,
# avisa sem falhar o setup (a instalacao dos drivers ja foi concluida).
if [[ -f "${CONNECT_TEST}" ]]; then
    info "Validando fonte: python connect_test.py --introspect ..."
    if python "${CONNECT_TEST}" --introspect; then
        ok "Conexao/introspeccao OK."
    elif python "${CONNECT_TEST}" --test; then
        ok "Conexao OK (via --test)."
    else
        die "connect_test.py falhou. Confira credenciais no .env e o driver do SO."
    fi
else
    warn "connect_test.py nao encontrado em ${AUTOMACAO_DIR}."
    warn "Drivers instalados; pulei a validacao de conexao."
fi

ok "================ FIM setup_source.sh (SOURCE=${SOURCE}) ================"
