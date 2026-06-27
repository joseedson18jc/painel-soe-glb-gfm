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

> Todos os números são **dados de demonstração sintéticos**.
