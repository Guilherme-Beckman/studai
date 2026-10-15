# TODO.md — Roadmap do StudAI (FUVEST)

> **Objetivo:** construir um segundo cérebro focado em estudar para a FUVEST: questões revisadas como fonte de verdade, classificação por uma taxonomia oficial, diagnóstico de lacunas e um plano diário de estudo com revisão espaçada.
>
> Este arquivo é o **roadmap do produto** e serve para o usuário acompanhar o progresso.
> Coordenação de quem está trabalhando em quê agora → [`WORK_STATUS.md`](WORK_STATUS.md). Regras gerais → [`AGENTS.md`](AGENTS.md).
>
> **Última atualização:** 2026-10-15

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
| 1 | Taxonomia | em andamento | 1/5 |
| 2 | Revisão de questões → fonte de verdade | pendente | 0/7 |
| 3 | Questões mal recortadas | pendente | 0/5 |
| 4 | Reorganização e otimização (código + IA) | pendente | 0/5 |
| 5 | Diagnóstico inicial | pendente | 0/5 |
| 6 | Revisão espaçada / "O que estudar hoje" | pendente | 0/6 |
| — | Expansão do acervo/RAG | bloqueada (depende das fases 1–3) | 0/3 |

---

## Fase 1 — Taxonomia

Base: `src/studai/features/classification/content_taxonomy.json`, `taxonomy.py`.

- [x] Árvore canônica hierárquica integrada ao classificador, revisão, RAG/embeddings, API, frontend e diagnósticos (commit fb01893, 2026-10-15)
- [ ] Auditar e integrar a proposta editorial `docs/fuvest-taxonomy-proposal.json` (fontes oficiais, IDs estáveis, granularidade)
- [ ] Mapear os rótulos legados (classificações antigas) para os IDs canônicos, com um relatório do que ficou sem correspondência
- [ ] Garantir que as folhas sejam específicas o suficiente para o diagnóstico (ex.: `Matemática → Trigonometria → Identidades → Arco duplo`)
- [ ] Manter `tests/test_content_taxonomy.py` cobrindo a estrutura (IDs únicos, caminhos válidos, fontes presentes)

## Fase 2 — Revisão de questões → fonte de verdade

Base: `src/studai/features/questions/review.py` (já tem os status `reviewed`/`unusable`, `reviewed_examples`, `write_reviewed_dataset`, `preserve_reviews`).

- [ ] Depois do upload de uma prova, abrir uma fila de revisão com todas as questões extraídas
- [ ] Botão de "check"/aprovar por questão, e aprovação em lote
- [ ] Edição na revisão: enunciado, alternativas, imagens, gabarito, matéria e conteúdo (IDs da taxonomia)
- [ ] Progresso de revisão por prova (aprovadas / pendentes / inutilizáveis / recorte ruim)
- [ ] Novo status `bad_crop` ("recorte ruim"), que manda a questão para a fila da Fase 3
- [ ] RAG e exemplos de classificação passam a usar **somente** questões aprovadas
- [ ] Testes do fluxo de revisão (`tests/test_question_review.py`)

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

## Fase 5 — Diagnóstico inicial

Base: `src/studai/features/diagnostics/service.py`, `diagnostico/cursos.json`.

- [ ] Montar o diagnóstico apenas com questões revisadas
- [ ] Métricas por nó da taxonomia (matéria → área → conteúdo → folha)
- [ ] Pesos por curso/carreira FUVEST (`diagnostico/cursos.json`)
- [ ] Seleção adaptativa de questões para cobrir as lacunas com menos questões
- [ ] Relatório de diagnóstico claro (pontos fortes, lacunas, prioridades)

## Fase 6 — Revisão espaçada / "O que estudar hoje"

- [ ] Escolher o algoritmo de agendamento (SM-2 ou FSRS) por conteúdo e por questão
- [ ] Modelo de dados no SQLite (histórico de respostas, próxima revisão, domínio por conteúdo)
- [ ] Caso de uso "o que estudar hoje" combinando o diagnóstico, as revisões vencidas e o peso do conteúdo na FUVEST
- [ ] Rota de API + tela "Hoje" no frontend
- [ ] Registrar as respostas do estudo diário e realimentar o agendamento
- [ ] Testes do agendador com datas simuladas

## Expansão do acervo/RAG (depois das fases 1–3)

- [ ] Processar mais provas de `fuvest_acervo/` usando o fluxo novo de revisão
- [ ] Revisar e aprovar as questões novas
- [ ] Medir o ganho do RAG com mais exemplos aprovados (`docs/benchmarks/`)

---

## Histórico

| Data | Agente / Branch | O que mudou | Commit |
|---|---|---|---|
| 2026-10-15 | — / `feature/classification/hierarchical-taxonomy` | Taxonomia canônica hierárquica | fb01893 |
| 2026-10-15 | Claude / `feature/classification/hierarchical-taxonomy` | Criado este TODO.md | — |
