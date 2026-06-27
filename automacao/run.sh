#!/usr/bin/env bash
#
# run.sh - Wrapper de producao para o ETL S&OE (painel GLB-GFM).
#
# Chamado pelo launchd 2x/dia (08:00 e 13:59 horario de Brasilia).
# Usa SOMENTE caminhos absolutos para funcionar fora de um shell interativo
# (o launchd nao herda o PATH/cwd do usuario).
#
#   Fluxo: ativa venv (se existir) -> roda soe_etl.py com flags de producao
#          -> gera ../data.json -> git push -> loga tudo em logs/.
#
set -euo pipefail

# --- Fuso horario: forca Brasilia em todo o processo (datas no data.json) ---
export TZ="America/Sao_Paulo"

# --- Caminhos absolutos (NAO dependem do diretorio de onde foi chamado) ---
AUTOMACAO_DIR="/Users/jose.costa/Desktop/painel-soe-glb-gfm/automacao"
VENV_DIR="${AUTOMACAO_DIR}/.venv"
ETL_SCRIPT="${AUTOMACAO_DIR}/soe_etl.py"
LOG_DIR="${AUTOMACAO_DIR}/logs"
ENV_FILE="${AUTOMACAO_DIR}/.env"

# --- Log do dia (logs/run-YYYYMMDD.log), append ---
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/run-$(date '+%Y%m%d').log"

# Tudo (stdout + stderr) com timestamp -> tela e arquivo.
log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$*" | tee -a "${LOG_FILE}"
}

# Redireciona todo o restante do script para o log (mantendo timestamps via stdbuf nao e portavel; usamos ts manual nas linhas-chave).
exec >> "${LOG_FILE}" 2>&1

log "================ INICIO run.sh (TZ=${TZ}) ================"

# --- Carrega .env (export de todas as variaveis nao-comentadas) ---
# ATENCAO: nao use comentarios inline na mesma linha de um valor no .env
# (ex.: VAR=valor # comentario) -> o '# comentario' entra no valor.
if [[ -f "${ENV_FILE}" ]]; then
    log "Carregando variaveis de ${ENV_FILE}"
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
else
    log "AVISO: ${ENV_FILE} nao encontrado. Rodando sem credenciais (pode falhar no source real)."
fi

# --- Ativa o virtualenv se existir; senao usa python3 do sistema ---
if [[ -f "${VENV_DIR}/bin/activate" ]]; then
    log "Ativando venv: ${VENV_DIR}"
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    PYTHON_BIN="python"
else
    log "venv nao encontrado em ${VENV_DIR}; usando python3 do sistema."
    PYTHON_BIN="python3"
fi

log "Python: $(command -v ${PYTHON_BIN}) ($(${PYTHON_BIN} --version 2>&1))"

# --- Flags de producao ---
# --source vem do .env (SOE_SOURCE: sql | powerbi | fixture). Default seguro: fixture.
SOURCE="${SOE_SOURCE:-fixture}"

log "Executando ETL: ${PYTHON_BIN} ${ETL_SCRIPT} --source ${SOURCE} --push"

# O ETL gera ../data.json e faz git push (flag --push = modo producao).
set +e
"${PYTHON_BIN}" "${ETL_SCRIPT}" --source "${SOURCE}" --push
RC=$?
set -e

if [[ ${RC} -eq 0 ]]; then
    log "ETL concluido com sucesso (rc=0)."
else
    log "ERRO: ETL retornou rc=${RC}."
fi

log "================ FIM run.sh (rc=${RC}) ================"
exit ${RC}
