# Análise Técnica — Painel Gerencial Planejamento GLB-GFM (S&OE, V4)

> Documento técnico orientado a arquitetura de software e a S&OE (Sales & Operations Execution). Avalia o painel HTML/JS single-file original (com simulador de cenários de estoque ideal) que serviu de base para as 3 versões deste repositório.

---

## A) Visão geral da arquitetura

### A.1 Modelo de distribuição: single-file
Artefato monolítico de página única (HTML + CSS + JS embutidos), servível como arquivo estático. Sem build, bundler, transpilação ou backend. Vantagens (portabilidade absoluta, abre por `file://`, zero deploy) e custos: sem modularização (JS em escopo global), sem versionamento de assets, difícil testar unitariamente.

### A.2 Bibliotecas (todas via CDN)
| Lib | Versão | Uso |
|---|---|---|
| SheetJS (`xlsx`) | 0.20.1 | Leitura XLSX/CSV; geração da planilha de cenários |
| jsPDF | 2.5.1 | Exportação PDF (landscape A4) |
| html2canvas | 1.4.1 | Captura visual para o PDF |
| Chart.js | 4.4.0 | Gráficos de barras |

**Risco:** 4 CDNs externos sem fallback local nem SRI → quebra offline/air-gapped e risco de supply chain.

### A.3 Tema visual
Dark navy executivo via CSS custom properties. Paleta semântica correta (verde=saudável, laranja=atenção, vermelho=ruptura, roxo=simulador). Adequado a um "war room" de planejamento.

### A.4 Estrutura de abas
5 abas, mas apenas **1 implementada**: Painel Executivo (vazia), Base Importada (vazia), Análise por SKU (vazia), **Simulador de Cenários (completa)**, Metodologia (vazia). Na prática, ~80% da navegação é casca.

### A.5 Estado global `G`
`{ rows, charts, activeTab:'exec', simRows, simResults, simDate }` — estado global mutável, sem reatividade. Funciona no escopo, mas escala mal.

---

## B) Caminhada do JavaScript — função a função

### B.1 Helpers
- **`gel(id)`** — alias de `getElementById`.
- **`esc(v)`** — escapa HTML antes de `innerHTML` (anti-XSS, se aplicado consistentemente).
- **`parseNum(v)`** — núcleo da robustez numérica: trata pt-BR (ponto=milhar, vírgula=decimal), remove `R$`/`t`. Retorna 0 em caso inválido (zero-silencioso mascara erros).
- **`norm(h)`** — normaliza cabeçalhos (minúsculas, sem acentos, `&`→`e`). Base do matching de colunas.
- **`week()`** — semana ISO **aproximada** (sem ajuste fino de fronteira de ano).
- **`clock()`** — atualiza data/hora a cada 1s (cosmético "LIVE").
- **`getZ(ns)`** — lookup discreta: 99→2.33, 97→1.88, 95→1.65, 90→1.28, senão→1.04 (não é a inversa da normal).

### B.2 Navegação e ciclo de vida
- **`switchTab(id)`** — show/hide via classe `.active`.
- **`init()`** — no `load`: `clock()` + `setInterval` + `simulateScenarios()` com defaults (abre já com 3 cenários).

### B.3 Gráficos
- **`dChart(id)`** — destrói instância anterior (evita leak/sobreposição).
- **`opts(y,legend)`** — fábrica de opções Chart.js (cores padronizadas).

### B.4 Entrada e arredondamento
- **`simVal(id)`** = `parseNum(gel(id).value)` — frágil: quebra se o elemento não existe.
- **`roundToLot(v,lote)`** — arredonda para cima ao múltiplo de lote (precisa de guarda p/ lote=0).
- **`getSimInputs()`** — lê os 12 campos do formulário.

### B.5 Núcleo — `scenarioCalc(nome, ns, pesoServico, pesoRent, base)`
```
z            = getZ(ns)
demDia       = demanda / 30
sigma        = demanda * 0.15                  // CV FIXO de 15%
estoqueSeg   = z * sigma * sqrt(ciclo/30)      // unidades inconsistentes
estoqueCiclo = demDia * ciclo / 2
estoqueAlvo  = estoqueCiclo + estoqueSeg
prodNecess.  = max(0, demanda + estoqueAlvo - estoque)
// ajuste por cenário: Serviço→max | Rentabilidade→min | Equilíbrio→média
prod         = roundToLot(prod, max(loteFam,loteBit)); prod = min(prod, capacidade)
estoqueFinal = estoque + prod - demanda
cobertura    = estoqueFinal / demDia
fill         = ns penalizado por ruptura/cobertura   // heurístico, não fill rate real
excesso      = max(0, estoqueFinal - estoqueAlvo); custoAdicional = excesso * custo
ebitdaProj.  = min(prod, demanda) * ebitda
score        = fill*pesoServico + max(0,ebitda/1000)*pesoRent - custoAdicional/1000
```

### B.6 Orquestração
- **`simulateScenarios()`** — 3 cenários (Serviço 99% [1.35/0.45], Rentabilidade 90% [0.55/1.35], Equilíbrio 95% [1.0/1.0]), ordena por score desc, recomenda o índice 0.
- **`renderSimResults()` / `renderSimCharts()`** — cartões + tabela de 12 colunas + 2 gráficos de barras.
- **`clearSimData()`** — zera formulário e estado.

### B.7 Importação/Exportação
- **`processSimFile(file)`** — lê XLSX/CSV, acha aba "Estoque Ideal", detecta cabeçalho (Código SKU + Plano S&OE + Estoque Livre Atual), lê metadados do topo, **agrega por planta** e simula. Trecho mais sofisticado e o mais frágil (acoplamento a nomes fixos).
- **`exportSimExcel()`** — planilha estruturada (bom).
- **`exportSimPDF()`** — "foto" via html2canvas (PDF como imagem, sem texto/acessibilidade).

---

## C) CSS/UX e HTML
- **Topbar** densa e executiva; dot "LIVE" é falso-positivo (só há relógio).
- **Tabs:** 4 de 5 levam a telas vazias — destrói percepção de completude; a aba default (`exec`) abre em branco.
- **`scenario-cards`:** ponto alto visual (comparação lado a lado, recomendado destacado).
- **Tabela de 12 colunas:** densa e adequada, mas risco de overflow horizontal.
- **Charts:** misturar estoque (un) + cobertura (dias) no mesmo gráfico exige eixo Y secundário.
- **Responsividade:** provável fragilidade < 1024px (tabela larga, inputs-grid, topbar densa).

---

## D) Bugs, riscos e fragilidades
1. **D.1 — 4 abas vazias (crítico):** abre numa tela em branco (`activeTab='exec'`).
2. **D.2 — Sigma fixo em 15%:** ignora variabilidade real por SKU → sub/super-proteção.
3. **D.3 — `sqrt(ciclo/30)` dimensionalmente inconsistente:** funciona "por acaso" só com ciclo=30.
4. **D.4 — `getZ` retorna 1.04 fora da faixa:** NS=98 dá proteção MENOR que NS=90.
5. **D.5 — `getZ`/Z=0 na importação:** safety stock vira 0 sem alerta → recomendação perigosa.
6. **D.6 — `simVal` quebra se o id não existe** (sem guarda `?.`).
7. **D.7 — Fill rate heurístico:** falsa precisão num KPI executivo.
8. **D.8 — Score soma unidades heterogêneas:** valor só ordinal (ranking), não interpretável.
9. **D.9 — Importação acoplada a nomes de aba/coluna fixos:** quebra silenciosa em variações de template.
10. **D.10 — Sem persistência:** recarregar perde tudo; sem histórico semanal (essencial p/ aderência).
11. **D.11 — Secundários:** CDNs sem fallback/SRI; `week()` aproximado; PDF como imagem; `roundToLot` com lote=0; `min(prod,demanda)*ebitda` ignora estoque inicial como fonte de venda.

---

## E) Pontos fortes do original
1. `parseNum` + `norm` robustos (pt-BR, R$/t, acentos).
2. Simulador multicritério funcional (serviço × rentabilidade × custo, com pesos).
3. Restrições físicas reais (lote mínimo, teto de capacidade).
4. Importação inteligente (detecção de cabeçalho, metadados, agregação por planta).
5. Higiene de render (`dChart/destroy`, `opts()`).
6. UX de decisão eficaz (3 cartões + tabela + motivo).
7. Deploy zero-friction (single-file).
8. Tema executivo coeso.

---

## F) Mapeamento para S&OE

| Vertical de S&OE | Cobertura atual | Lacuna / ação |
|---|---|---|
| Balanço demanda × suprimento | **Forte** (núcleo do simulador) | Falta visão multi-SKU/multi-planta simultânea |
| Cobertura de estoque (DOS) | Parcial | Falta semáforo (mín/máx/alvo) e projeção rolante |
| Capacidade | Parcial (teto) | Falta utilização %, gargalos, alerta de estouro |
| Gestão de exceções | Fraco | Fila priorizada (ruptura/excesso/NS) com owner e ação |
| OTIF / Fill rate | Ausente (heurístico) | Fill rate e OTIF medidos (realizado vs prometido) |
| Aderência ao plano | Ausente | Plano × Realizado por semana (KPI central; exige persistência) |
| Acuracidade (MAPE/Bias) | Ausente | MAPE/WMAPE e viés → fecha o loop com sigma real |
| Saúde financeira | Parcial | Capital empatado, write-off de excesso, margem real vs plano |
| Cadência semanal | Cosmético | Snapshot semanal versionado; corrigir `week()` ISO |

### Veredito
Bom **motor de simulação de reposição** embrulhado num **invólucro de S&OE incompleto**: entrega ~1 das ~9 verticais de forma sólida (balanço demanda×suprimento). Para virar uma ferramenta de S&OE de fato, o esforço maior **não é o cálculo** (que existe e é razoável) — é a **camada de execução**: histórico, aderência, KPIs realizados (OTIF/fill/MAPE) e gestão de exceções, mais o saneamento dos vícios matemáticos (sigma fixo, getZ degrau, dimensionalidade do LT).

> **As 3 versões deste repositório atacam exatamente essas lacunas** — preenchendo as abas vazias com OTIF/fill, aderência, MAPE/viés, capacidade, cobertura com semáforo e gestão de exceções acionável, mantendo (e aprimorando) o motor de simulação.
