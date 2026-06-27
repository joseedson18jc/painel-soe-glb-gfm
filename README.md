# Painéis Gerenciais S&OE · GLB-GFM

Três painéis executivos de **S&OE (Sales & Operations Execution)** — execução de vendas e operações no horizonte de 0 a 13 semanas. Cada painel é um arquivo HTML único, autossuficiente, com gráficos dinâmicos (Chart.js), dados de demonstração pré-carregados e simulador de cenários.

## 🔗 Acesso (GitHub Pages)

- **Hub / Página inicial:** `index.html`
- **V1 — Cockpit Executivo** (`cockpit-executivo.html`): visão de comando para diretoria — semáforo de saúde, gauges de OTIF e aderência ao plano, radar S&OE, balanço demanda × suprimento e cadência semanal. Tema dark premium.
- **V2 — Torre de Controle** (`torre-controle.html`): operação orientada a exceções — quadro kanban de alertas (ruptura, excesso, capacidade, atraso), drill-down de SKU e fluxo Demanda → Produção → Estoque → Atendimento. Tema claro/clean.
- **V3 — Laboratório de Cenários** (`laboratorio-cenarios.html`): what-if profundo — simulador com sliders ao vivo, fronteira de trade-off Serviço × Custo × Caixa, acuracidade (MAPE/viés) e distribuição de risco. Tema dark analítico.

## 🧮 Metodologia

| Métrica | Fórmula | Leitura |
|---|---|---|
| Estoque de segurança | `z · σ · √LT` | Z por nível de serviço: 90%→1,28; 95%→1,65; 97%→1,88; 99%→2,33 |
| Cobertura (dias) | `Estoque ÷ Demanda diária` | Verde ≥15d · Amarelo 7–15d · Vermelho <7d |
| OTIF / Fill Rate | On-Time-In-Full | KPI central de execução |
| Score de cenário | `f(serviço, EBITDA, custo)` | Base da recomendação automática |

## ⚙️ Verticais de S&OE cobertas

Nível de serviço/OTIF · Aderência ao plano · Balanço demanda × suprimento · Cobertura e saúde de estoque · Gestão de exceções/alertas · Capacidade e utilização · Acuracidade de previsão (MAPE/viés) · Saúde financeira · Simulador de cenários.

## 🛠️ Tecnologia

HTML/CSS/JS puro · [Chart.js 4.4](https://www.chartjs.org/) · [SheetJS](https://sheetjs.com/) (Excel) · jsPDF + html2canvas (PDF) · fonte Inter. Sem build, sem backend — basta abrir no navegador.

> Todos os números são **dados de demonstração sintéticos** quando a automação não está ativa.

## 🔄 Automação (atualização 2x/dia)

Quando ativada, a pasta [`automacao/`](automacao/) mantém os painéis atualizados **automaticamente, duas vezes por dia**, sem intervenção manual.

**Fluxo:**

```
SQL Server / Power BI  →  soe_etl.py  →  data.json  →  git push  →  GitHub Pages
                          (08:00 e 13:59 BRT)                         (publica)
                               │
                               └──→  e-mail de alerta → emiliodias1@gmail.com
                                     (ruptura · excesso · capacidade · falha de ETL)
```

- **Fonte:** banco **SQL** (`SOE_SOURCE=sql`) ou dataset **Power BI / DAX** (`SOE_SOURCE=powerbi`); modo demo via `fixture` (SQLite local).
- **Processamento:** `soe_etl.py` recalcula os KPIs de S&OE e roda uma simulação **Monte Carlo** de risco (acelerada por `numpy` quando disponível), gravando `data.json`.
- **Publicação:** `git push` aciona o **GitHub Pages**; os três painéis passam a ler o `data.json` mais recente.
- **Alerta em tempo real:** o dashboard lê o `data.json` e destaca os alertas (rupturas/excessos/atrasos) assim que a página carrega — quem abrir o painel já vê o estado mais recente, e os casos críticos também saem por e-mail para `emiliodias1@gmail.com`.
- **Agendamento:** `launchd` no Mac dispara o wrapper [`automacao/run.sh`](automacao/run.sh) às **08:00** e **13:59** (horário de Brasília).

➡️ **Passo a passo de instalação e ativação:** [`automacao/SETUP.md`](automacao/SETUP.md). Só ative o `launchd` depois de preencher o `.env`.
