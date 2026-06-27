#!/usr/bin/env python3
"""Cria o fixture SQLite (soe.db) com ~16 SKUs siderurgicos realistas.

Tabelas:
  skus        -> uma linha por SKU com parametros de demanda/oferta/financeiro.
  metadados   -> chave/valor (nivel de servico meta, lead time padrao, etc).
  linhas      -> capacidades de linha (capacidade_t / utilizado_t).

Numeros sao COERENTES: o data.json resultante e plausivel e sem alertas
absurdos (poucos criticos, a maioria saudavel/atencao). 100% stdlib.

Uso:  python3 fixtures/seed_sqlite.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "soe.db"

# Colunas: ver sources.py (EXPECTED_COLUMNS). Unidades:
#   demanda_sem_t, plano_t, estoque_t, producao_real_t, sigma_sem_t,
#   safety_stock_t, demanda_prev_t, demanda_real_t em TONELADAS.
#   lead_time_dias em dias. preco_rs_t / ebitda_rs_t / custo_estoque_rs_t em R$/t.
#   otif_pct em %.
SKUS = [
    # sku, familia, bitola, demanda_sem, sigma_sem, plano, estoque, prod_real,
    # demanda_prev, demanda_real, otif, lead_time, preco, ebitda_rs_t, custo_estoque_rs_t
    ("VG-CA50-8.0",  "Vergalhao CA-50",   "8.0mm",  1200, 132, 1180, 2600, 1175, 1180, 1205, 96.5, 12, 3850, 540, 38),
    ("VG-CA50-10",   "Vergalhao CA-50",   "10mm",   1500, 150, 1490, 3100, 1505, 1480, 1520, 97.2, 12, 3820, 555, 36),
    ("VG-CA50-12.5", "Vergalhao CA-50",   "12.5mm", 1350, 162, 1320, 2400, 1310, 1330, 1372, 94.8, 12, 3790, 520, 37),
    ("VG-CA50-16",   "Vergalhao CA-50",   "16mm",    900, 117,  880, 1500,  885,  890,  912, 95.1, 14, 3760, 505, 39),
    ("FM-SAE1008-5.5", "Fio-Maquina",     "5.5mm",  2000, 180, 1980, 4200, 1990, 1970, 2025, 97.8, 10, 3640, 480, 33),
    ("FM-SAE1008-6.4", "Fio-Maquina",     "6.4mm",  1700, 170, 1690, 1100, 1680, 1660, 1735, 91.2, 10, 3610, 470, 34),
    ("PRF-U-100",    "Perfil U/I",        "U 100",   650,  84,  640, 1380,  642,  640,  661, 95.9, 18, 4980, 690, 52),
    ("PRF-I-200",    "Perfil U/I",        "I 200",   480,  72,  470,  980,  475,  470,  489, 94.4, 18, 5120, 720, 54),
    ("CH-GR-12.7",   "Chapa Grossa",      "12.7mm",  820, 115,  800,  430,  805,  790,  848, 88.7, 25, 6450, 880, 61),
    ("CH-GR-19",     "Chapa Grossa",      "19mm",    600,  78,  600, 1320,  598,  600,  612, 96.8, 25, 6600, 910, 63),
    ("CH-GR-25.4",   "Chapa Grossa",      "25.4mm",  410,  61,  400,  900,  402,  400,  418, 95.0, 28, 6720, 935, 64),
    ("TB-SC-114",    "Tubo Sem Costura",  "114.3mm", 320,  54,  310,  150,  308,  300,  331, 86.5, 35, 9200, 1280, 88),
    ("TB-SC-168",    "Tubo Sem Costura",  "168.3mm", 260,  39,  260,  720,  262,  260,  266, 97.1, 35, 9450, 1320, 90),
    ("AR-RC-2.0",    "Arame Recozido",    "2.0mm",   540,  59,  540, 1180,  544,  540,  551, 96.2,  9, 4220, 410, 31),
    ("AR-GLV-2.7",   "Arame Galvanizado", "2.7mm",   470,  61,  460,  640,  458,  460,  478, 92.9,  9, 4780, 520, 35),
    ("TR-TR57",      "Trilho",            "TR-57",   210,  42,  200,  560,  205,  200,  223, 90.1, 40, 8800, 1150, 95),
]

METADADOS = {
    "ns_meta_pct": "95.0",
    "lead_time_padrao_dias": "14",
    "semana_base": "S26",
    "ano_base": "2026",
    "ebitda_meta_mi": "40.0",
}

# linha, capacidade_t, utilizado_t
LINHAS = [
    ("Laminacao Quente", 9500, 8550),
    ("Trefilaria",       4200, 3700),
    ("Aciaria",          11000, 9460),
    ("Acabamento",       3800, 3100),
    ("Laminacao Perfis", 1600, 1120),
]


def build(db_path: Path = DB_PATH) -> Path:
    """(Re)cria o banco de fixture e retorna o caminho."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE skus (
                sku TEXT PRIMARY KEY,
                familia TEXT NOT NULL,
                bitola TEXT NOT NULL,
                demanda_sem_t REAL NOT NULL,
                sigma_sem_t REAL,
                plano_t REAL NOT NULL,
                estoque_t REAL NOT NULL,
                producao_real_t REAL NOT NULL,
                demanda_prev_t REAL NOT NULL,
                demanda_real_t REAL NOT NULL,
                otif_pct REAL NOT NULL,
                lead_time_dias REAL NOT NULL,
                preco_rs_t REAL NOT NULL,
                ebitda_rs_t REAL NOT NULL,
                custo_estoque_rs_t REAL NOT NULL
            )
            """
        )
        cur.executemany(
            "INSERT INTO skus VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", SKUS
        )
        cur.execute("CREATE TABLE metadados (chave TEXT PRIMARY KEY, valor TEXT)")
        cur.executemany(
            "INSERT INTO metadados VALUES (?,?)", list(METADADOS.items())
        )
        cur.execute(
            """
            CREATE TABLE linhas (
                linha TEXT PRIMARY KEY,
                capacidade_t REAL NOT NULL,
                utilizado_t REAL NOT NULL
            )
            """
        )
        cur.executemany("INSERT INTO linhas VALUES (?,?,?)", LINHAS)
        conn.commit()
    finally:
        conn.close()
    return db_path


if __name__ == "__main__":
    path = build()
    print(f"[seed_sqlite] OK -> {path} ({len(SKUS)} SKUs, {len(LINHAS)} linhas)")
