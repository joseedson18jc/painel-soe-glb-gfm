# Setup da automacao S&OE (atualizacao 2x/dia)

Passo a passo para colocar o ETL do painel GLB-GFM rodando sozinho, duas vezes
por dia (08:00 e 13:59, horario de Brasilia), via `launchd` no Mac.

> **Fluxo:** `soe_etl.py` le a fonte (SQL ou Power BI) -> recalcula KPIs e o
> Monte Carlo de risco -> grava `../data.json` -> faz `git push` (GitHub Pages
> publica) -> dispara e-mail de alerta para `emiliodias1@gmail.com` quando ha
> ruptura/excesso/falha. O dashboard le o `data.json` e mostra os alertas.

> **REGRA DE OURO:** so ative o `launchd` (passo 6) **depois** de preencher o
> `.env` (passo 2/3). Sem credencial preenchida, o ETL roda sem fonte real e
> pode publicar dados vazios ou falhar.

---

## 1. Criar a venv e instalar dependencias do adaptador escolhido

O nucleo roda so com a biblioteca padrao (a fonte `fixture` e o e-mail ja
funcionam sem instalar nada). Instale apenas o que o seu adaptador exige.

```bash
cd /Users/jose.costa/Desktop/painel-soe-glb-gfm/automacao
python3 -m venv .venv
source .venv/bin/activate

# Abra requirements.txt e DESCOMENTE so o bloco do seu caso:
#   - numpy            -> acelera o Monte Carlo (opcional sempre)
#   - SQLAlchemy+pyodbc -> se SOE_SOURCE=sql
#   - requests+msal     -> se SOE_SOURCE=powerbi
pip install -r requirements.txt
```

> `pyodbc` exige o driver ODBC do SO (ex.: `msodbcsql18`). No Mac:
> `brew install msodbcsql18` (via tap da Microsoft).

---

## 2. Gerar a SENHA DE APP do Gmail e preencher o `.env`

O e-mail de alerta usa SMTP do Gmail. **Nao** use a senha normal da conta: o
Gmail exige uma "Senha de app" (precisa de verificacao em 2 etapas ativa).

1. Acesse **myaccount.google.com**.
2. **Seguranca** -> ative a **Verificacao em 2 etapas** (se ainda nao tiver).
3. Em **Seguranca** -> **Senhas de app** (ou busque "Senhas de app").
4. Crie uma senha de app (ex.: nome "painel-soe"). O Google mostra **16
   caracteres**. Copie-os **sem espacos**.

Depois copie o exemplo e preencha:

```bash
cp .env.example .env
# edite .env e preencha:
#   SOE_GMAIL_USER=suaconta@gmail.com
#   SOE_GMAIL_APP_PASSWORD=os16caracteres   (sem espacos)
#   ALERT_TO=emiliodias1@gmail.com
```

> **Gotcha do `.env`:** nunca use comentario inline na linha de um valor
> (`MC_SAMPLES=50000   # ...`) — o comentario vira parte do valor. Comentarios
> so em linhas proprias.

---

## 3. Configurar a fonte de dados (SQL **ou** Power BI)

Escolha **um** adaptador e ajuste `SOE_SOURCE` no `.env`.

### Opcao A — SQL (`SOE_SOURCE=sql`)

Preencha `SOE_SQL_URL` e `SOE_SQL_QUERY`. A query precisa devolver as colunas
com os **aliases do contrato** que o ETL espera:

```sql
-- Exemplo (ajuste tabelas/joins a sua base):
SELECT
    sku                          AS sku,
    descricao                    AS descricao,
    familia                      AS familia,
    semana                       AS semana,            -- ISO week ou data da segunda-feira
    demanda_prevista             AS demanda,
    producao_planejada           AS producao,
    estoque_atual                AS estoque,
    estoque_seguranca            AS estoque_seguranca,
    lead_time_dias               AS lead_time,
    desvio_padrao_demanda        AS sigma_demanda,
    nivel_servico_alvo           AS nivel_servico,
    otif                         AS otif,
    capacidade_utilizada         AS capacidade
FROM vw_plano_soe
WHERE semana BETWEEN @inicio AND @fim
ORDER BY familia, sku, semana;
```

Exemplo de `SOE_SQL_URL` (SQL Server):

```
mssql+pyodbc://usuario:senha@host:1433/BaseSOE?driver=ODBC+Driver+18+for+SQL+Server&Encrypt=yes&TrustServerCertificate=no
```

### Opcao B — Power BI (`SOE_SOURCE=powerbi`)

Preencha `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`,
`PBI_WORKSPACE_ID`, `PBI_DATASET_ID` (service principal com acesso ao
workspace) e `PBI_DAX` com uma consulta que devolva as mesmas colunas:

```dax
EVALUATE
SELECTCOLUMNS(
    FILTER(
        PlanoSOE,
        PlanoSOE[Semana] >= DATE(2026,1,1)
    ),
    "sku",               PlanoSOE[SKU],
    "descricao",         PlanoSOE[Descricao],
    "familia",           PlanoSOE[Familia],
    "semana",            PlanoSOE[Semana],
    "demanda",           PlanoSOE[DemandaPrevista],
    "producao",          PlanoSOE[ProducaoPlanejada],
    "estoque",           PlanoSOE[EstoqueAtual],
    "estoque_seguranca", PlanoSOE[EstoqueSeguranca],
    "lead_time",         PlanoSOE[LeadTimeDias],
    "sigma_demanda",     PlanoSOE[DesvioDemanda],
    "nivel_servico",     PlanoSOE[NivelServicoAlvo],
    "otif",              PlanoSOE[OTIF],
    "capacidade",        PlanoSOE[CapacidadeUtilizada]
)
```

> Os nomes dos campos retornados devem bater com os aliases do contrato. Em caso
> de duvida sobre o contrato exato, confira o cabecalho do `soe_etl.py`.

---

## 4. Habilitar `git push` nao-interativo (para o cron/launchd)

O `launchd` roda sem terminal: o git **nao pode** pedir usuario/senha. Configure
o helper de credencial do GitHub CLI uma vez:

```bash
gh auth login        # se ainda nao estiver autenticado
gh auth setup-git    # configura o git para usar o token do gh (push sem prompt)
```

Teste que o push funciona sem pedir nada:

```bash
cd /Users/jose.costa/Desktop/painel-soe-glb-gfm
git push            # nao deve pedir senha
```

---

## 5. TESTE manual

Antes de agendar, rode o wrapper na mao e confira o log:

```bash
cd /Users/jose.costa/Desktop/painel-soe-glb-gfm/automacao
./run.sh
tail -n 50 logs/run-$(date +%Y%m%d).log
```

Confira: `data.json` foi atualizado, o `git push` saiu, e (se havia alerta) o
e-mail chegou em `emiliodias1@gmail.com`.

---

## 6. ATIVAR o agendamento (launchd)

> So faca isto **depois** do `.env` preenchido e do teste manual OK.

```bash
cp launchd/com.glbgfm.soe.0800.plist  ~/Library/LaunchAgents/
cp launchd/com.glbgfm.soe.1359.plist  ~/Library/LaunchAgents/

launchctl load ~/Library/LaunchAgents/com.glbgfm.soe.0800.plist
launchctl load ~/Library/LaunchAgents/com.glbgfm.soe.1359.plist
```

Verifique que estao carregados:

```bash
launchctl list | grep com.glbgfm.soe
```

> **Fuso horario:** o `launchd` dispara os horarios no **horario LOCAL do Mac**.
> Estes plists assumem o Mac em **America/Sao_Paulo**. Se o Mac estiver em outro
> fuso, edite a chave `Hour`/`Minute` em cada `.plist` para o equivalente local
> de 08:00 e 13:59 BRT. (A variavel `TZ` dos plists so afeta as datas geradas
> dentro do processo, **nao** o horario do gatilho.)

### Para DESATIVAR / atualizar

```bash
launchctl unload ~/Library/LaunchAgents/com.glbgfm.soe.0800.plist
launchctl unload ~/Library/LaunchAgents/com.glbgfm.soe.1359.plist
```

Depois de editar um `.plist`, sempre faca `unload` -> copie de novo -> `load`
para o launchd reler a configuracao.

---

## Logs e troubleshooting

- `logs/run-YYYYMMDD.log` — log do `run.sh` (todo o ETL, com timestamps).
- `logs/launchd-0800.*.log` / `logs/launchd-1359.*.log` — stdout/stderr que o
  launchd captura (util quando o `run.sh` nem chega a iniciar).
- Push pedindo senha no log? Refaca o passo 4 (`gh auth setup-git`).
- Nao disparou no horario? Confira `launchctl list | grep glbgfm` e o fuso do
  Mac (passo 6).
