"""Configuracao central da automacao S&OE.

- Parser .env em stdlib puro (NAO depende de python-dotenv).
- Constantes de negocio (timezone, dias uteis, custos, capacidades).
- Helpers de tempo no fuso America/Sao_Paulo (zoneinfo) com UTC em paralelo.

Todas as funcoes sao puras/testaveis e nao tem efeito colateral alem de ler o
ambiente/.env uma unica vez no import.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

try:  # zoneinfo e stdlib em 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - fallback defensivo
    ZoneInfo = None  # type: ignore

logger = logging.getLogger("soe.config")

# --------------------------------------------------------------------------- #
# Caminhos                                                                     #
# --------------------------------------------------------------------------- #
# .../automacao/soe/config.py -> AUTOMACAO_DIR = .../automacao ; REPO = pai
AUTOMACAO_DIR: Path = Path(__file__).resolve().parent.parent
REPO_DIR: Path = AUTOMACAO_DIR.parent
DEFAULT_OUT: Path = REPO_DIR / "data.json"
FIXTURE_DB: Path = AUTOMACAO_DIR / "fixtures" / "soe.db"
LOG_DIR: Path = AUTOMACAO_DIR / "logs"
ENV_FILE: Path = AUTOMACAO_DIR / ".env"

BRT_TZ_NAME = "America/Sao_Paulo"

# --------------------------------------------------------------------------- #
# Constantes de negocio (S&OE siderurgico)                                     #
# --------------------------------------------------------------------------- #
DIAS_UTEIS_SEMANA: float = 7.0          # demanda_sem distribuida em 7 dias corridos
SEMANAS_HORIZONTE: int = 8              # horizonte do balanco/cobertura
CRITICO_DIAS: float = 7.0              # cobertura < 7 -> critico
ATENCAO_DIAS: float = 15.0            # 7..15 -> atencao ; >=15 -> saudavel
EXCESSO_FATOR: float = 1.5            # cobertura > alvo*1.5 -> excesso
NS_META_DEFAULT: float = 95.0        # nivel de servico alvo padrao (%)
CV_FALLBACK_FAMILIA: float = 0.18    # CV usado SOMENTE como fallback (gera issue)

# pesos do health score por dimensao do radar (somam 1.0)
HEALTH_WEIGHTS: Dict[str, float] = {
    "servico": 0.30,
    "estoque": 0.20,
    "capacidade": 0.15,
    "acuracidade": 0.20,
    "caixa": 0.15,
}

DASHBOARD_URL = "https://joseedson18jc.github.io/painel-soe-glb-gfm/"


# --------------------------------------------------------------------------- #
# Parser .env (stdlib puro)                                                    #
# --------------------------------------------------------------------------- #
def parse_env_file(path: Path) -> Dict[str, str]:
    """Le um arquivo .env simples (KEY=VALUE) sem dependencias externas.

    Regras:
      - linhas em branco e comecadas por '#' sao ignoradas;
      - 'export ' no inicio e removido;
      - aspas simples/duplas ao redor do valor sao retiradas;
      - GOTCHA conhecido: NAO removemos comentarios inline (apos '#') de valores
        sem aspas, pois isso ja quebrou o pipeline de noticias antes. O valor e
        usado literalmente ate o fim da linha (apenas .strip()).
    """
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            env[key] = value
    return env


def _load_env() -> Dict[str, str]:
    """Carrega .env e sobrepoe com variaveis ja presentes em os.environ."""
    merged = parse_env_file(ENV_FILE)
    for k, v in os.environ.items():
        if k.startswith(("SOE_", "PBI_", "ALERT_", "MC_")):
            merged[k] = v
    return merged


_ENV = _load_env()


def get(key: str, default: Optional[str] = None) -> Optional[str]:
    """Le uma chave do ambiente combinado (.env + os.environ)."""
    return _ENV.get(key, default)


def get_int(key: str, default: int) -> int:
    try:
        return int(str(_ENV.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float) -> float:
    try:
        return float(str(_ENV.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Settings tipados                                                             #
# --------------------------------------------------------------------------- #
@dataclass
class Settings:
    """Snapshot imutavel das configuracoes resolvidas em runtime."""

    source: str = field(default_factory=lambda: (get("SOE_SOURCE", "sqlite") or "sqlite"))
    mc_samples: int = field(default_factory=lambda: get_int("MC_SAMPLES", 50000))
    ns_meta: float = field(default_factory=lambda: get_float("SOE_NS_META", NS_META_DEFAULT))

    # SQL
    sql_url: Optional[str] = field(default_factory=lambda: get("SOE_SQL_URL"))
    sql_query: Optional[str] = field(default_factory=lambda: get("SOE_SQL_QUERY"))

    # Power BI
    pbi_tenant: Optional[str] = field(default_factory=lambda: get("PBI_TENANT_ID"))
    pbi_client_id: Optional[str] = field(default_factory=lambda: get("PBI_CLIENT_ID"))
    pbi_client_secret: Optional[str] = field(default_factory=lambda: get("PBI_CLIENT_SECRET"))
    pbi_dataset_id: Optional[str] = field(default_factory=lambda: get("PBI_DATASET_ID"))
    pbi_group_id: Optional[str] = field(default_factory=lambda: get("PBI_GROUP_ID"))
    pbi_dax: Optional[str] = field(default_factory=lambda: get("PBI_DAX_QUERY"))

    # E-mail
    gmail_user: Optional[str] = field(default_factory=lambda: get("SOE_GMAIL_USER"))
    gmail_pass: Optional[str] = field(default_factory=lambda: get("SOE_GMAIL_APP_PASSWORD"))
    alert_to: str = field(default_factory=lambda: get("ALERT_TO", "emiliodias1@gmail.com") or "emiliodias1@gmail.com")
    smtp_host: str = field(default_factory=lambda: get("SOE_SMTP_HOST", "smtp.gmail.com") or "smtp.gmail.com")
    smtp_port: int = field(default_factory=lambda: get_int("SOE_SMTP_PORT", 587))

    # Git
    git_branch: str = field(default_factory=lambda: get("SOE_GIT_BRANCH", "main") or "main")


SETTINGS = Settings()


# --------------------------------------------------------------------------- #
# Tempo / fuso                                                                 #
# --------------------------------------------------------------------------- #
def now_utc() -> datetime:
    """Agora em UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def to_brt(dt: datetime) -> datetime:
    """Converte um datetime aware para America/Sao_Paulo (fallback UTC-3 fixo)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if ZoneInfo is not None:
        try:
            return dt.astimezone(ZoneInfo(BRT_TZ_NAME))
        except Exception:  # pragma: no cover
            pass
    from datetime import timedelta

    return dt.astimezone(timezone(timedelta(hours=-3)))


def iso_utc(dt: datetime) -> str:
    """ISO-8601 em UTC com sufixo Z (segundos)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fmt_brt(dt: datetime) -> str:
    """'YYYY-MM-DD HH:MM:SS' no fuso BRT."""
    return to_brt(dt).strftime("%Y-%m-%d %H:%M:%S")


def run_label(dt: datetime) -> str:
    """Rotulo da execucao com base na hora BRT.

    Convencao: rodadas matinais ~08:00 e vespertinas ~13:59. Escolhemos o
    rotulo mais proximo do horario real para nao mentir no e-mail/dash.
    """
    brt = to_brt(dt)
    minutes = brt.hour * 60 + brt.minute
    # ponto medio entre 08:00 (480) e 13:59 (839) = ~659 (~10:59)
    return "08:00 BRT" if minutes < 660 else "13:59 BRT"


def iso_week_label(dt: datetime) -> str:
    """Semana ISO no formato 'S{semana}/{ano2}'. Usa o fuso BRT."""
    brt = to_brt(dt)
    iso = brt.isocalendar()  # (year, week, weekday)
    year2 = iso[0] % 100
    return f"S{iso[1]:02d}/{year2:02d}"


def short_week_id(dt: datetime) -> str:
    """Identificador curto 'S{semana}' (sem ano) p/ balanco/cadencia."""
    return f"S{to_brt(dt).isocalendar()[1]:02d}"


def ddmm(dt: datetime) -> str:
    """'dd/mm' no fuso BRT (p/ cadencia)."""
    return to_brt(dt).strftime("%d/%m")


def ensure_dirs() -> None:
    """Garante que diretorios de saida/logs existam."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    FIXTURE_DB.parent.mkdir(parents=True, exist_ok=True)
