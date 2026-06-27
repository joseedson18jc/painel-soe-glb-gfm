# Conectar o ETL S&OE a uma fonte REAL

Guia passo-a-passo para ligar o ETL do painel GLB-GFM a um banco SQL ou a um
dataset do Power BI. Quando o `connect_test` passar, voce roda o `./run.sh` e
ativa o `launchd` (ver **SETUP.md**).

> **Pre-requisito comum:** a venv ja criada e ativa.
>
> ```bash
> cd /Users/jose.costa/Desktop/painel-soe-glb-gfm/automacao
> python3 -m venv .venv
> source .venv/bin/activate
> ```

---

## O CONTRATO (fonte unica de verdade)

Qualquer fonte — SQL ou Power BI — **deve devolver exatamente estas 15 colunas**
(aliases). A lista canonica vive em `soe/sources.py` -> `EXPECTED_COLUMNS`. O ETL
**ignora** colunas com qualquer outro nome: **alias errado = dado vazio**.

| alias                | tipo  | significado                              |
|----------------------|-------|------------------------------------------|
| `sku`                | texto | codigo do SKU                            |
| `familia`            | texto | familia do produto                       |
| `bitola`             | texto | bitola                                   |
| `demanda_sem_t`      | t     | demanda da semana                        |
| `sigma_sem_t`        | t     | desvio-padrao semanal (**pode ser NULL**)|
| `plano_t`            | t     | plano de producao                        |
| `estoque_t`          | t     | estoque atual                            |
| `producao_real_t`    | t     | producao realizada                       |
| `demanda_prev_t`     | t     | demanda prevista (p/ MAPE/vies)          |
| `demanda_real_t`     | t     | demanda realizada (p/ MAPE/vies)         |
| `otif_pct`           | %     | OTIF do SKU                              |
| `lead_time_dias`     | dias  | lead time                                |
| `preco_rs_t`         | R$/t  | preco de venda                           |
| `ebitda_rs_t`        | R$/t  | margem EBITDA                            |
| `custo_estoque_rs_t` | R$/t  | custo de carregar estoque                |

- `sku`/`familia`/`bitola` sao **texto**; as demais sao **numericas** (t / R$ / % / dias).
- Apenas `sigma_sem_t` pode vir **NULL** — as outras numericas devem ter valor.

O nucleo do ETL roda em **stdlib pura**. As libs externas (SQLAlchemy/pyodbc,
msal/requests) sao importadas de forma **LAZY** — voce so instala as do caminho
que escolher.

---

## Como funciona o `.env`

Toda credencial vai no arquivo `.env` (copiado de `.env.example`). Ele e
**ignorado pelo git** — segredo **NUNCA** entra no repositorio.

```bash
cp .env.example .env
# edite .env com seu editor
```

> **GOTCHA do parser `.env` (stdlib):** **NAO** use comentario inline na linha
> de um valor. O parser usa a linha inteira ate o fim.
>
> ```
> ERRADO:  SOE_NS_META=95   # nivel de servico   <- o comentario vira parte do valor
> CERTO:   SOE_NS_META=95
> ```
>
> Comentarios **so** em linhas proprias, comecando com `#`.

Variaveis lidas por `soe/config.py` (`SETTINGS`):

| .env                 | campo `SETTINGS`     | usado por        |
|----------------------|----------------------|------------------|
| `SOE_SOURCE`         | `source`             | seletor de fonte |
| `SOE_SQL_URL`        | `sql_url`            | SQL              |
| `SOE_SQL_QUERY`      | `sql_query`          | SQL              |
| `SOE_LINHAS_JSON`    | `config.get(...)`    | SQL/PBI (opc.)   |
| `SOE_NS_META`        | `ns_meta`            | SQL/PBI          |
| `PBI_TENANT_ID`      | `pbi_tenant`         | Power BI         |
| `PBI_CLIENT_ID`      | `pbi_client_id`      | Power BI         |
| `PBI_CLIENT_SECRET`  | `pbi_client_secret`  | Power BI         |
| `PBI_DATASET_ID`     | `pbi_dataset_id`     | Power BI         |
| `PBI_GROUP_ID`       | `pbi_group_id`       | Power BI (opc.)  |
| `PBI_DAX_QUERY`      | `pbi_dax`            | Power BI         |

---

## Como rodar o `connect_test`

Sempre teste a conexao **antes** de `run.sh`/`launchd`. O `connect_test.py` usa
o `SOE_SOURCE` do `.env` (ou exportado no shell) e tem 3 modos:

```bash
# 1) Conecta na fonte ativa e confere os 15 aliases do contrato:
python3 connect_test.py test --verbose

# 2) Lista as colunas CRUAS que a fonte devolveu (acha alias errado/faltante):
python3 connect_test.py --introspect

# 3) Gera o esqueleto da query (SQL ou DAX, conforme SOE_SOURCE) com os 15 aliases:
python3 connect_test.py --map
```

`test` sai com codigo `0` quando a conexao funciona **e** os 15 aliases chegaram.
Use `--map` para gerar a query inicial e `--introspect` para depurar quando
`test` reclamar de coluna faltando.

---

## Caminho A — SQL Server (o mais comum sob Power BI)

### Pre-requisitos

1. **Driver ODBC da Microsoft no Mac** (o `pyodbc` exige o driver do SO):

   ```bash
   brew tap microsoft/mssql-release https://github.com/microsoft/homebrew-mssql-release
   brew update
   HOMEBREW_ACCEPT_EULA=Y brew install msodbcsql18 mssql-tools18
   ```

2. **Libs Python** (descomente o bloco SQL em `requirements.txt` ou instale direto):

   ```bash
   pip install sqlalchemy pyodbc
   ```

3. **Rede:** o Mac precisa alcancar o servidor na **porta 1433/TCP**. Em rede
   corporativa isso quase sempre exige **VPN ligada** e liberacao de firewall
   (o IP do Mac no allowlist do SQL Server). Teste a porta:

   ```bash
   nc -vz HOST 1433
   ```

### Passos

1. Monte o `SOE_SQL_URL` (formato SQLAlchemy + pyodbc). Modelo:

   ```
   mssql+pyodbc://USUARIO:SENHA@HOST:1433/BaseSOE?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
   ```

   - `Encrypt=yes` e o padrao do Driver 18 (recomendado).
   - Use `TrustServerCertificate=yes` **apenas** se o servidor tiver certificado
     self-signed (homologacao).
   - Senha com caracteres especiais (`@ : / ?`): faca **URL-encode**
     (ex.: `@` -> `%40`).

2. Gere a query base e escreva `SOE_SQL_QUERY` com os 15 aliases:

   ```bash
   SOE_SOURCE=sql python3 connect_test.py --map
   ```

   Exemplo final (ajuste origem/joins; **mantenha os aliases**):

   ```sql
   SELECT
       cod_sku             AS sku,
       familia             AS familia,
       bitola              AS bitola,
       demanda_semana_t    AS demanda_sem_t,
       desvio_demanda_t    AS sigma_sem_t,      -- pode ser NULL
       plano_producao_t    AS plano_t,
       estoque_atual_t     AS estoque_t,
       producao_real_t     AS producao_real_t,
       demanda_prevista_t  AS demanda_prev_t,
       demanda_realizada_t AS demanda_real_t,
       otif                AS otif_pct,
       lead_time_dias      AS lead_time_dias,
       preco_venda         AS preco_rs_t,
       margem_ebitda       AS ebitda_rs_t,
       custo_carregamento  AS custo_estoque_rs_t
   FROM vw_plano_soe
   ORDER BY familia, sku;
   ```

   No `.env`, valor em **uma unica linha** (ou entre aspas se for multi-linha;
   sem comentario inline).

3. Preencha o `.env`:

   ```
   SOE_SOURCE=sql
   SOE_SQL_URL=mssql+pyodbc://USUARIO:SENHA@HOST:1433/BaseSOE?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
   SOE_SQL_QUERY=SELECT cod_sku AS sku, ... FROM vw_plano_soe ORDER BY familia, sku;
   ```

4. Teste:

   ```bash
   python3 connect_test.py test --verbose       # conexao + contrato
   python3 connect_test.py --introspect         # se faltar coluna, veja o cru
   ```

> **Capacidades de linha (opcional):** defina `SOE_LINHAS_JSON` no `.env` para as
> metricas de capacidade/utilizacao. Ex.:
> `SOE_LINHAS_JSON=[{"linha":"Laminacao","capacidade_t":5000,"utilizado_t":4300}]`

---

## Caminho B — PostgreSQL / MySQL

### Pre-requisitos

```bash
# PostgreSQL:
pip install sqlalchemy psycopg2-binary
# MySQL / MariaDB:
pip install sqlalchemy pymysql
```

(Postgres/MySQL **nao** precisam de driver ODBC do SO — so as libs Python acima.)

### Passos

1. Monte o `SOE_SQL_URL`:

   ```
   # PostgreSQL:
   postgresql+psycopg2://USUARIO:SENHA@HOST:5432/BaseSOE
   # MySQL / MariaDB:
   mysql+pymysql://USUARIO:SENHA@HOST:3306/BaseSOE
   ```

   - URL-encode na senha quando houver caractere especial.
   - Postgres usa **5432**; MySQL **3306** (libere no firewall / VPN se preciso).

2. Escreva `SOE_SQL_QUERY` com os 15 aliases (mesma logica do Caminho A —
   `connect_test.py --map` gera o esqueleto). SGBD diferente, **aliases iguais**.

3. Preencha o `.env` (`SOE_SOURCE=sql`, `SOE_SQL_URL`, `SOE_SQL_QUERY`) e teste:

   ```bash
   python3 connect_test.py test --verbose
   python3 connect_test.py --introspect
   ```

---

## Caminho C — Power BI (dataset via DAX)

O ETL chama `POST https://api.powerbi.com/v1.0/myorg/datasets/{PBI_DATASET_ID}/executeQueries`
com a sua `PBI_DAX_QUERY`, autenticando como **service principal** (Azure AD /
Entra ID). O prefixo `Tabela[coluna]` do DAX e removido automaticamente.

### Pre-requisitos

```bash
pip install msal requests
```

### 1. Registrar o service principal no Azure AD (Entra ID)

1. Portal Entra (**entra.microsoft.com**) -> **App registrations** -> **New registration**.
2. Anote **Directory (tenant) ID** -> `PBI_TENANT_ID` e
   **Application (client) ID** -> `PBI_CLIENT_ID`.
3. **Certificates & secrets** -> **New client secret** -> copie o **Value**
   (so aparece uma vez) -> `PBI_CLIENT_SECRET`.

### 2. Liberar service principals no Power BI

1. **Power BI Admin portal** -> **Tenant settings** -> **Developer settings** ->
   habilite **"Allow service principals to use Power BI APIs"** (de preferencia
   restrito a um grupo de seguranca que contenha o app).
2. Garanta que **XMLA endpoint / executeQueries** esteja habilitado para o
   workspace (capacidade Premium/PPU/Fabric; em **Read** ou **Read Write**).

### 3. Dar acesso ao workspace e pegar os IDs

1. No workspace do dataset -> **Access** -> adicione o app (service principal)
   como **Member** ou **Contributor**.
2. Abra o dataset -> a URL traz os IDs:
   `.../groups/{PBI_GROUP_ID}/datasets/{PBI_DATASET_ID}/...`
   - `PBI_DATASET_ID` -> obrigatorio.
   - `PBI_GROUP_ID` (workspace) -> **opcional** para a chamada `executeQueries`.

### 4. Escrever a `PBI_DAX_QUERY`

```bash
SOE_SOURCE=powerbi python3 connect_test.py --map
```

Modelo (`SELECTCOLUMNS` com os 15 aliases entre aspas — ajuste a tabela de origem):

```dax
EVALUATE
SELECTCOLUMNS(
    PlanoSOE,
    "sku",                PlanoSOE[SKU],
    "familia",            PlanoSOE[Familia],
    "bitola",             PlanoSOE[Bitola],
    "demanda_sem_t",      PlanoSOE[DemandaSemana],
    "sigma_sem_t",        PlanoSOE[DesvioDemanda],
    "plano_t",            PlanoSOE[PlanoProducao],
    "estoque_t",          PlanoSOE[EstoqueAtual],
    "producao_real_t",    PlanoSOE[ProducaoRealizada],
    "demanda_prev_t",     PlanoSOE[DemandaPrevista],
    "demanda_real_t",     PlanoSOE[DemandaRealizada],
    "otif_pct",           PlanoSOE[OTIF],
    "lead_time_dias",     PlanoSOE[LeadTimeDias],
    "preco_rs_t",         PlanoSOE[PrecoVenda],
    "ebitda_rs_t",        PlanoSOE[MargemEBITDA],
    "custo_estoque_rs_t", PlanoSOE[CustoCarregamento]
)
```

### 5. Preencher o `.env` e testar

```
SOE_SOURCE=powerbi
PBI_TENANT_ID=...
PBI_CLIENT_ID=...
PBI_CLIENT_SECRET=...
PBI_DATASET_ID=...
PBI_GROUP_ID=
PBI_DAX_QUERY=EVALUATE SELECTCOLUMNS(PlanoSOE, "sku", PlanoSOE[SKU], ... )
```

```bash
python3 connect_test.py test --verbose
python3 connect_test.py --introspect
```

---

## Troubleshooting

| Erro / sintoma                                                         | Causa provavel                                                     | Solucao                                                                                          |
|------------------------------------------------------------------------|-------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| `Can't open lib 'ODBC Driver 18 for SQL Server'` / `IM002`            | driver ODBC nao instalado no Mac                                  | `brew tap microsoft/mssql-release && brew install msodbcsql18`; confira o nome no `driver=` da URL |
| `ModuleNotFoundError: pyodbc` / `sqlalchemy` / `msal` / `requests`    | lib do adaptador nao instalada                                    | SQL: `pip install sqlalchemy pyodbc` (ou `psycopg2-binary`/`pymysql`); PBI: `pip install msal requests` |
| `Login failed for user` / `28000` / `auth` falha                     | usuario/senha errados ou senha mal URL-encoded                    | confira credenciais; URL-encode caracteres especiais (`@`->`%40`); cheque permissao do login na base |
| `Falha ao obter token Power BI` / `AADSTS...`                         | client secret expirado/errado, tenant/client ID trocados, SP nao liberado | regere o secret; confira `PBI_TENANT_ID`/`PBI_CLIENT_ID`; habilite service principals no tenant Power BI |
| timeout / `nc: connection refused` / conexao trava                   | porta bloqueada (1433/5432/3306), VPN desligada, IP fora do allowlist | ligue a VPN; libere a porta no firewall; adicione o IP do Mac no allowlist do servidor; `nc -vz HOST PORTA` |
| `[ERRO] colunas do contrato ausentes` / aliases faltando             | a query nao usa os aliases exatos do contrato                     | rode `connect_test.py --introspect` p/ ver o cru; corrija os `AS alias` (SQL) ou `"alias"` (DAX) p/ os 15 nomes |
| `executeQueries` 401/403 / "dataset not found" / sem permissao       | SP sem acesso ao workspace, XMLA/executeQueries off, dataset ID errado | de acesso ao SP no workspace; habilite XMLA/executeQueries (Premium/PPU/Fabric); confira `PBI_DATASET_ID` |
| `source '...' retornou 0 SKUs`                                        | query valida mas sem linhas (filtro/where vazio)                  | rode a query direto no SGBD/Power BI; ajuste filtros; confirme que ha dados no periodo            |
| comentario "virou" parte do valor / URL/query truncada               | comentario inline no `.env`                                       | tire o `# ...` da linha do valor; comentarios so em linhas proprias                              |
| `git push` pedindo usuario/senha (no `run.sh`/launchd)               | git sem credencial nao-interativa                                 | `gh auth login` e depois `gh auth setup-git`; teste com `git push` (nao deve pedir nada)         |

---

## Depois que o `connect_test` passar

1. **Rode o ETL na mao** e confira o log:

   ```bash
   ./run.sh
   tail -n 50 logs/run-$(date +%Y%m%d).log
   ```

2. **Ative o agendamento (`launchd`)** seguindo o **SETUP.md** (passo 6): copie os
   `.plist` para `~/Library/LaunchAgents/` e faca `launchctl load`. Lembre da
   **REGRA DE OURO**: so ative o `launchd` **depois** do `.env` preenchido e do
   `connect_test` + teste manual OK.
