# TODO.md — Roadmap do StudAI (FUVEST)

> **Objetivo:** construir um segundo cérebro focado em estudar para a FUVEST: questões revisadas como fonte de verdade, classificação por uma taxonomia oficial, diagnóstico de lacunas e um plano diário de estudo com revisão espaçada.
>
> Este arquivo é o **roadmap do produto** e serve para o usuário acompanhar o progresso.
> Coordenação de quem está trabalhando em quê agora → [`WORK_STATUS.md`](WORK_STATUS.md). Regras gerais → [`AGENTS.md`](AGENTS.md).
>
> **Última atualização:** 2026-10-19

---

## Como atualizar (obrigatório para qualquer agente)

1. **Ao começar** um item: troque `[ ]` por `[~]` e anote `(em andamento: <agente>, <branch>)`.
2. **Ao concluir**: troque por `[x]` somente se o trabalho estiver testado (`python -m unittest discover -s tests`) e commitado. Anote `(commit <hash>, AAAA-MM-DD)`.
3. **Descobriu trabalho novo?** Acrescente um sub-item na fase certa. Nunca apague itens: se um item deixar de fazer sentido, risque (`~~item~~`) e explique o motivo.
4. **Atualize o Resumo** (contagem feitos/total) e a data de **Última atualização**.
5. **Acrescente uma linha no Histórico** no fim do arquivo.

Legenda: `[ ]` pendente · `[~]` em andamento · `[x]` concluído

---

## Resumo do progresso

| # | Fase | Status | Feitos/Total |
|---|---|---|---|
| 1 | Taxonomia | em andamento | 6/7 |
| 2 | Revisão de questões → fonte de verdade | concluída | 8/8 |
| 3 | Questões mal recortadas | pendente | 0/5 |
| 4 | Reorganização e otimização (código + IA) | em andamento | 4/13 |
| 5 | Diagnóstico inicial | concluída | 6/6 |
| 6 | Revisão espaçada / "O que estudar hoje" | concluída | 6/6 |
| — | Expansão do acervo/RAG | bloqueada (depende das fases 1–3) | 0/3 |

---

## Fase 1 — Taxonomia

Base: `src/studai/features/classification/content_taxonomy.json`, `taxonomy.py`.

- [x] Árvore canônica hierárquica integrada ao classificador, revisão, RAG/embeddings, API, frontend e diagnósticos (commit fb01893, 2026-10-15)
- [x] Auditar e integrar a proposta editorial `docs/fuvest-taxonomy-proposal.json` (fontes oficiais, IDs estáveis, granularidade): versão `fuvest-2027.v7`, fontes com página do guia 2026 e relatório por matéria em [`docs/taxonomy-review-v7.md`](docs/taxonomy-review-v7.md) (2026-10-15)
- [x] Mapear os rótulos legados (classificações antigas) para os IDs canônicos, com um relatório do que ficou sem correspondência: `legacy_mapping.py`, CLI `studai.tools.map_legacy_labels`, relatório [`docs/legacy-label-mapping.md`](docs/legacy-label-mapping.md); 477 de 478 rótulos mapeados, só "REVISAR" sem correspondência; nenhum registro reescrito (2026-10-15)
- [x] Garantir que as folhas sejam específicas o suficiente para o diagnóstico: árvore com 4 níveis em todas as 13 matérias, matéria → área → conteúdo → subconteúdo, com 1.457 subconteúdos (2026-10-15)
  - [x] Matemática: 189 subconteúdos (`level: "subcontent"`), Trigonometria sem grupos redundantes, versão `fuvest-2027.v2` (commit cebcec1, 2026-10-15)
  - [x] Física: 177 subconteúdos, versão `fuvest-2027.v3` (commit 3bc0cdf, 2026-10-15)
  - [x] Química: 164 subconteúdos, versão `fuvest-2027.v4` (commit 1818d83, 2026-10-15)
  - [x] Biologia: 203 subconteúdos, versão `fuvest-2027.v5` (commit 1d27818, 2026-10-15)
  - [x] Humanas e Linguagens, versão `fuvest-2027.v6` (2026-10-15): História 177, Geografia 122, Filosofia 72, Sociologia 81, Língua Portuguesa 88, Literatura 63, Inglês 40, Arte 42, Educação Física 39
  - [x] Passada editorial por IA em todas as 13 matérias (`fuvest-2027.v7`): 1.457 → 1.703 subconteúdos ativos, 5 nós marcados `deprecated` com `replaced_by` (2026-10-15)
  - [ ] Revisão editorial humana dos subconteúdos de todas as matérias (gerados editorialmente, marcados com `editorial_detail: true`); dúvidas por matéria em [`docs/taxonomy-review-v7.md`](docs/taxonomy-review-v7.md)
  - [x] Física: "Leis da termodinâmica e máquinas térmicas" movida da área Mecânica para Termologia e termodinâmica (v7)
  - [x] Biologia: "Vírus" e "Bactérias, protistas e fungos" viraram conteúdos próprios; grupos vegetais ficaram em "Adaptações das plantas ao ambiente terrestre" (v7)
- [x] Classificação em duas etapas: 1ª etapa escolhe o conteúdo e a 2ª refina entre os subconteúdos; se não houver subconteúdo confiável, a questão fica com `subconteudo_pendente` para revisão (2026-10-15)
- [ ] Medir a acurácia do classificador em duas etapas com as questões revisadas (`docs/benchmarks/`) e reconstruir o índice de embeddings de tópicos
  - [x] `evaluate_classifier.py` mede a 1ª etapa (`content_stage1`) e o refinamento (`refinement`) (2026-10-15)
  - [ ] Rodar depois da nova rodada de revisão humana; o rótulo de subconteúdo ainda não tem gabarito
    ```bash
    PYTHONPATH=src .venv/bin/python -m studai.tools.evaluate_classifier simulados/cache/reviewed-topic-eval.jsonl --provider ollama --model qwen3.5:4b --output simulados/cache/two-stage-qwen3.5-4b.json --timeout 180
    ```
- [x] Manter `tests/test_content_taxonomy.py` cobrindo a estrutura (IDs únicos, caminhos válidos, fontes presentes, aliases sem colisão, nós `deprecated`) (2026-10-15)

## Fase 2 — Revisão de questões → fonte de verdade

Base: `src/studai/features/questions/review.py` (já tem os status `reviewed`/`unusable`, `reviewed_examples`, `write_reviewed_dataset`, `preserve_reviews`).

- [x] Depois do upload de uma prova, abrir uma fila de revisão com todas as questões extraídas: todas as questões têm ações, não só as sinalizadas pela IA (commit 0c715a3, 2026-10-16)
- [x] Botão de "check"/aprovar por questão, e aprovação em lote (`POST /api/exams/<id>/reviews/bulk`, usa matéria/conteúdo da IA e devolve as puladas com motivo) (commit 0c715a3, 2026-10-16)
- [x] Edição na revisão: enunciado, alternativas, imagens (remover + enviar nova), gabarito, matéria e conteúdo (IDs da taxonomia). As correções ficam em `revisao.json` (`edits`) por cima da extração, que não muda (commit 0c715a3, 2026-10-16)
- [x] Progresso de revisão por prova (aprovadas / pendentes / inutilizáveis / recorte ruim), agora sobre todas as questões (commit 0c715a3, 2026-10-16)
- [x] Novo status `bad_crop` ("recorte ruim"), que manda a questão para a fila da Fase 3 (`GET /api/review/bad-crops`); fica fora do diagnóstico e do "Responder prova" (commit 0c715a3, 2026-10-16)
- [x] RAG e exemplos de classificação passam a usar **somente** questões aprovadas, com o texto corrigido; pool do diagnóstico também aplica as correções (commit 0c715a3, 2026-10-16)
- [x] Testes do fluxo de revisão (`tests/test_question_review.py`, `tests/test_question_reprocessing.py`) (commit 0c715a3, 2026-10-16)
- [x] Filtros na revisão: status, sinalização, matéria, conteúdo, confiança IA < 70%, com imagem, sem gabarito, editadas e busca por número/texto (lembrados por prova no navegador) (commit 0c715a3, 2026-10-16)

## Fase 3 — Questões mal recortadas

Base: `src/studai/features/questions/extractor.py`, `batch.py`.

- [ ] Levantar casos reais (prova/ano/número) e classificar as causas: duas colunas, imagens laterais, texto-base compartilhado entre questões, figura em outra página
- [ ] Modo de extração alternativo para esses casos (ex.: recorte manual por região na UI e/ou render da página + modelo de visão)
- [ ] Reprocessar uma questão isolada sem perder a revisão já feita (reaproveitar `preserve_reviews`)
- [ ] Associar corretamente as imagens e o texto-base compartilhado a cada questão
- [ ] Fixtures de teste com os casos problemáticos (`tests/test_question_reprocessing.py`, `tests/test_math_question_extraction.py`)

## Fase 4 — Reorganização e otimização (código + leituras/análises de IA)

- [ ] Inventariar todas as chamadas de IA (extração, classificação, embeddings, diagnóstico): modelo, prompt, custo e tempo
- [ ] Eliminar chamadas duplicadas e cachear resultados (`STUDAI_CACHE_DIR`)
- [ ] Consolidar os scripts da raiz (`extract.py`, `classifier.py`, `diagnostico_core.py`…) em `src/studai/`
- [ ] Medir antes/depois com `benchmark.py` e registrar em `docs/benchmarks/`
- [ ] Atualizar `ARCHITECTURE.md` com a estrutura final
- [x] Classificação mais rápida (2026-10-15; detalhes no README, seção "Velocidade da classificação")
  - [x] Roteamento hierárquico: matéria → conteúdo da matéria → subconteúdo, com prompts de sistema fixos e cerca de 8× menores (`classify_routed`; `STUDAI_CLASSIFICATION_ROUTING=flat` volta ao modo antigo)
  - [x] Ollama registra o tempo de leitura do prompt e a etapa de cada chamada; `evaluate_classifier` soma por etapa
  - [x] Pool: faixas grátis Groq e Hugging Face via `.env`, limite de RPM por faixa, pausa até a meia-noite em cota diária, pool compartilhado entre uploads simultâneos
  - [x] Lotes para nuvem (`STUDAI_CLASSIFY_BATCH`): várias questões por request em cada etapa
  - [x] `benchmark_topic_retrieval --per-subject`: recall dos conteúdos por embeddings dentro da matéria
- [x] Medir o roteamento hierárquico contra o modo plano (`--limit 10`, qwen3.5:4b): 2,8× mais rápido (76 s contra 212 s por questão), conteúdo da 1ª etapa 50% contra 20%; ver `docs/benchmarks/two-stage-routing.md` (2026-10-15)
- [x] Ordenar o processamento local por etapa (flag `STUDAI_STAGE_ORDER`): medido sem ganho (483 s contra 496 s em 10 questões). O código foi removido e fica no commit cf44202; ver `docs/benchmarks/two-stage-routing.md` (commit cf44202, 2026-10-15)
- [x] Pôr o enunciado antes das instruções de cada etapa (flag `STUDAI_QUESTION_FIRST`): medido 26% **mais lento** (85 s contra 68 s por questão, questões 11 a 20). O qwen3.5 é híbrido, e o llama.cpp só retoma o cache em checkpoints a 4 e a ~1 024 tokens do fim do prompt, nunca no fim da questão. Acurácia igual ou melhor (qwen 8 contra 6 de 10 no conteúdo; Gemini 14 contra 14 de 20). O código foi removido e fica no commit f2430e2; ver `docs/benchmarks/two-stage-routing.md` (2026-10-15)
- [ ] Testar um modelo local sem camadas recorrentes (o cache de prefixo volta a valer para qualquer prefixo) ou o llama.cpp direto com checkpoint no fim da questão
- [ ] Melhorar regras e descrições de Inglês × Língua Portuguesa × Literatura (canções e poemas confundem a matéria)
- [x] Medir recall por matéria: top-15 de 92,7% (qwen3-embedding) e 82,9% (bge-m3), abaixo de 98%, então o pré-filtro **não** foi ativado (2026-10-15)
- [ ] Testar o backend Vulkan do Ollama na iGPU AMD e medir com `evaluate_classifier --limit 10`
- [ ] Depois de mais revisões: classificador leve de matéria (regressão logística/SetFit sobre embeddings bge-m3, revisões + BLUEX) para substituir a 1ª chamada quando a confiança for alta

## Fase 5 — Diagnóstico inicial

Base: `src/studai/features/diagnostics/service.py`, `diagnostico/cursos.json`.

- [x] Montar o diagnóstico apenas com questões revisadas: critério único `review.diagnostic_ready` (pool + exemplos RAG) (commit 43fa4fa, 2026-10-19)
- [x] Métricas por nó da taxonomia (matéria → área → conteúdo → folha): posterior Beta por nó, IC 80%, status forte/lacuna/incerto/não testado (`diagnostics/mastery.py`) (commits 41822e8, 43fa4fa, 2026-10-19)
- [x] Pesos por curso/carreira FUVEST (`diagnostico/cursos.json` v2): derivados de NF = (F1 + D1 + D2)/3 do Guia de Provas 2027; Eng. Computação São Carlos ≈ Mat 25% · Fís 25% · Redação 17% fora do banco (`diagnostics/courses.py`) (commit 41822e8, 2026-10-19)
- [x] Seleção adaptativa de questões para cobrir as lacunas com menos questões: cobertura por matéria primeiro, depois área → conteúdo → folha por peso × incidência × incerteza; para por orçamento ou convergência (commit 43fa4fa, 2026-10-19)
- [x] Relatório de diagnóstico claro (pontos fortes, lacunas, prioridades com critério de saída) (`diagnostics/report.py`) (commit 43fa4fa, 2026-10-19)
- [x] Painel "Relatórios" com a evolução por conteúdo (API `/api/reports/*`, `frontend/reports.js`) e respostas do "Responder prova" salvas no histórico (commits 57badef, 5095fe5, 2026-10-19)

## Fase 6 — Revisão espaçada / "O que estudar hoje"

- [x] Escolher o algoritmo de agendamento (SM-2 ou FSRS) por conteúdo e por questão: FSRS (`fsrs==6.3.2`), cartão por conteúdo e por questão errada (commit 0ac86e9, 2026-10-19)
- [x] Modelo de dados no SQLite (histórico de respostas, próxima revisão, domínio por conteúdo): `review_cards`, `review_logs`, `study_sessions`, `study_answers` (commit 0ac86e9, 2026-10-19)
- [x] Caso de uso "o que estudar hoje" combinando o diagnóstico, as revisões vencidas e o peso do conteúdo na FUVEST (`study/service.py`) (commit 0ac86e9, 2026-10-19)
- [x] Rota de API + tela "Hoje" no frontend (commit 0ac86e9, 2026-10-19)
- [x] Registrar as respostas do estudo diário e realimentar o agendamento; respostas dos diagnósticos também entram (commit 0ac86e9, 2026-10-19)
- [x] Testes do agendador com datas simuladas (`tests/test_study_scheduler.py`) (commit 0ac86e9, 2026-10-19)

## Expansão do acervo/RAG (depois das fases 1–3)

- [ ] Processar mais provas de `fuvest_acervo/` usando o fluxo novo de revisão
- [ ] Revisar e aprovar as questões novas
- [ ] Medir o ganho do RAG com mais exemplos aprovados (`docs/benchmarks/`)

---

## Histórico

| Data | Agente / Branch | O que mudou | Commit |
|---|---|---|---|
| 2026-10-15 | — / `feature/classification/hierarchical-taxonomy` | Taxonomia canônica hierárquica | fb01893 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Criado este TODO.md | 19957a1 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Matemática aprofundada até subconteúdos; classificador em duas etapas | cebcec1 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Física aprofundada até subconteúdos | 3bc0cdf |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Química aprofundada até subconteúdos | 1818d83 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Biologia aprofundada até subconteúdos | 1d27818 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Humanas e Linguagens aprofundadas até subconteúdos (9 matérias) | c767bbd |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Avaliador mede 1ª etapa e refinamento; benchmark adiado para depois da revisão humana | — |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Roteamento hierárquico, faixas grátis com cota no pool, lotes para nuvem, medições por etapa | 3af7324 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Benchmark hierárquico vs plano e recall de embeddings por matéria | — |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Ordem por etapa no Ollama local (flag `STUDAI_STAGE_ORDER`), medida sem ganho | cf44202 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Enunciado antes das instruções (flag `STUDAI_QUESTION_FIRST`), medido 26% mais lento no qwen3.5 | f2430e2 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Remoção das flags `STUDAI_STAGE_ORDER` e `STUDAI_QUESTION_FIRST`, que não deram ganho | — |
| 2026-10-15 | Claude (agentes em paralelo) / `feature/classification/hierarchical-taxonomy` | Taxonomia `fuvest-2027.v7`: proposta integrada, passada editorial nas 13 matérias, nós `deprecated`, mapeamento de rótulos legados, testes de estrutura | — |
| 2026-10-16 | Claude / `claude/question-reprocessing-formulas-0dda57` | Fase 2: fila de revisão completa, edições em camada, aprovação em lote, `bad_crop`, gabarito manual, filtros | 0c715a3 |
| 2026-10-19 | Claude / `claude/fase-6-revisao-espacada` | Fase 6: revisão espaçada FSRS, tela "Hoje", sincronização com os diagnósticos | 0ac86e9 |
| 2026-10-19 | Claude (swarm de 3 agentes) / `feature/diagnostics/fase5-diagnostico-relatorios` | Fase 5: diagnóstico adaptativo só com revisadas, domínio por nó, pesos FUVEST derivados, relatório e painel de evolução | 5095fe5 |
