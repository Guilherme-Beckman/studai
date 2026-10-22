# TODO.md — Roadmap do StudAI (FUVEST)

> **Objetivo:** construir um segundo cérebro focado em estudar para a FUVEST: questões revisadas como fonte de verdade, classificação por uma taxonomia oficial, diagnóstico de lacunas e um plano diário de estudo com revisão espaçada.
>
> Este arquivo é o **roadmap do produto** e serve para o usuário acompanhar o progresso.
> Coordenação de quem está trabalhando em quê agora → [`WORK_STATUS.md`](WORK_STATUS.md). Regras gerais → [`AGENTS.md`](AGENTS.md).
>
> **Última atualização:** 2026-10-22

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
| 3 | Questões mal recortadas | concluída | 7/7 |
| 4 | Reorganização e otimização (código + IA) | concluída | 13/13 |
| 5 | Diagnóstico inicial | concluída | 6/6 |
| 6 | Revisão espaçada / "O que estudar hoje" | concluída | 6/6 |
| 7 | Mapa do conhecimento e progresso por profundidade | concluída | 5/5 |
| 8 | Modo foco (tela cheia) para responder questões | concluída | 6/6 |
| 9 | Treinamento contínuo | concluída | 6/6 |
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

- [x] Levantar casos reais (prova/ano/número) e classificar as causas: duas colunas, imagens laterais, texto-base compartilhado entre questões, figura em outra página — [`docs/bad-crops-survey.md`](docs/bad-crops-survey.md). As 450 questões revisadas de 2022–2026 estavam todas aprovadas, mas 3 tinham recorte quebrado (2023 Q28, 2025 Q1, 2026 Q42): a revisão confere texto e classificação, não o recorte (commit 2f9e620, 2026-10-20)
- [x] Modo de extração alternativo para esses casos: recorte manual por região na revisão ("Recortar região do PDF", `frontend/crop-editor.js`), com o texto da região sugerido para enunciado e alternativas; salvo em `edits` do `revisao.json`, sem mudar a extração (`questions/manual_crop.py`, `source_pdf.py`) (commits b42217a, 5770748, 2026-10-20)
  - [ ] Opcional: modo com modelo de visão sobre o render da página
- [x] Reprocessar uma questão isolada sem perder a revisão já feita: lógica saiu do `server.py` para o caso de uso `questions/reprocess.py`, mesma API; herda as decisões da extração anterior quando falta `revisao.json` (commits b42217a, 9934944, 2026-10-20)
- [x] Associar corretamente as imagens e o texto-base compartilhado a cada questão: fundo branco atrás do texto não vira mais figura (2023 Q28, 2026 Q42), página de rascunho ignorada (2024 Q90), recorte alarga para a faixa de página inteira (2025 Q1), `enunciado_vazio` sinalizado mesmo com figura (commit 63aed1b, 2026-10-20)
- [x] Fixtures de teste com os casos problemáticos: PDFs sintéticos em `tests/test_questions_bad_crops.py`, `tests/test_questions_ocr.py`, `tests/test_questions_manual_crop.py`, `tests/test_questions_reprocess_usecase.py` (2026-10-20)
- [x] Extrator para os formatos de 2021 para trás (commit b7b922b, 2026-10-19). Todas as provas de 1998 a 2021 com camada de texto saem com o total de questões (80 por dia em 1998–2002, 100 em 2003–2006, 90 depois); antes, de 1998 a 2018 só 2011 passava. Testes em `tests/test_questions_legacy_layouts.py`
  - alternativas `a)` (até 2017), número colado ao texto em negrito/corpo maior (`07 Na…`, `V.01 – …`), 100 questões, páginas carta e ampliadas, capa/contracapa e folha de gabarito ignoradas, número de página e título de matéria fora do texto, caixa "Dados:" como nota
  - ordem de leitura: faixa de página inteira entre colunas (2015, 2017), blocos reordenados pelo número (2005 Q45), linhas largas picadas no PDF; números desenhados como imagens de dígitos (2018)
  - 2021: o texto inteiro é fonte Type3 em bitmap sem Unicode; saem as 90 questões sem texto (o lixo é descartado) e caem em `enunciado_vazio`, com o recorte da página para revisão
- [x] OCR para as provas sem camada de texto legível: Tesseract (`por`) via subprocess, automático nas páginas sem texto ou com lixo, `STUDAI_OCR=0` desliga (`questions/ocr.py`). Questões com texto: 2021 0 → 89/90, 1997 dia 1 0 → 76/80, dia 2 0 → 77/80, 2002 dia 2 0 → 76/80; sem regressão nas outras 44 provas (commits 776e424, 0e7182b, 2b75759, 2026-10-20)
  - [x] OCR por coluna (calha achada no render, `--psm 4` por coluna), `por+eng` (`STUDAI_OCR_LANG`), alternativas lado a lado e linhas duplicadas separadas, 2ª passada `--psm 11` nas questões sem alguma letra. Alternativas incompletas: 2021 19 → 4, 2002 dia 2 13 → 9, 1997 24/28 → 17/16; 1998–2020 com `questoes.json` idêntico; 300 dpi mantido (400 perde o número da 2021 Q9) — [`docs/benchmarks/legacy-ocr.md`](docs/benchmarks/legacy-ocr.md) (2026-10-22)
  - [ ] 2021: Q11, Q46 e Q83 (letra colada a fórmula ou fração, linha perdida) e Q90 (alternativas desenhadas) seguem incompletas
  - 1996 fica fora do escopo (modelo muito diferente: uma imagem por questão com "Resposta Correta X"); o foco é 2010 em diante
  - [ ] 1997 e 2002 dia 2 ainda perdem 1 número de questão cada (79/80): passada `--psm 6` entre as questões vizinhas, se essas provas voltarem ao escopo

## Fase 4 — Reorganização e otimização (código + leituras/análises de IA)

- [x] Inventariar todas as chamadas de IA (extração, classificação, embeddings, diagnóstico): modelo, prompt, custo e tempo — [`docs/ai-calls-inventory.md`](docs/ai-calls-inventory.md) (commit 8d5b1d0, 2026-10-20)
- [x] Eliminar chamadas duplicadas e cachear resultados (`STUDAI_CACHE_DIR`): `integrations/ai_cache.py` guarda respostas validadas e vetores de embedding; `STUDAI_AI_CACHE=off` desliga (commit 09d3856, 2026-10-20)
  - [ ] A chave do cache da classificação inclui a versão do índice RAG, então qualquer revisão reclassifica a prova inteira; usar os exemplos efetivamente recuperados por questão na chave
- [x] Consolidar os scripts da raiz (`extract.py`, `classifier.py`, `diagnostico_core.py`…) em `src/studai/`: `fuvest_scraper.py` foi para `studai.tools`; um teste garante que nenhum script da raiz tenha lógica própria (commit 8e086fb, 2026-10-20)
- [x] Medir antes/depois com `benchmark.py` e registrar em `docs/benchmarks/`: `python benchmark.py ai-calls`, offline; numa prova sintética de 30 questões, textos de embedding 133 → 43, reprocessar após revisão 30 → 0 embeddings — [`docs/benchmarks/ai-calls-cache.md`](docs/benchmarks/ai-calls-cache.md) (commits e387352, 82451df, 2026-10-20)
- [x] Atualizar `ARCHITECTURE.md` com a estrutura final (commit 8d5b1d0, 2026-10-20)
- [x] Classificação mais rápida (2026-10-15; detalhes no README, seção "Velocidade da classificação")
  - [x] Roteamento hierárquico: matéria → conteúdo da matéria → subconteúdo, com prompts de sistema fixos e cerca de 8× menores (`classify_routed`; `STUDAI_CLASSIFICATION_ROUTING=flat` volta ao modo antigo)
  - [x] Ollama registra o tempo de leitura do prompt e a etapa de cada chamada; `evaluate_classifier` soma por etapa
  - [x] Pool: faixas grátis Groq e Hugging Face via `.env`, limite de RPM por faixa, pausa até a meia-noite em cota diária, pool compartilhado entre uploads simultâneos
  - [x] Lotes para nuvem (`STUDAI_CLASSIFY_BATCH`): várias questões por request em cada etapa
  - [x] `benchmark_topic_retrieval --per-subject`: recall dos conteúdos por embeddings dentro da matéria
- [x] Medir o roteamento hierárquico contra o modo plano (`--limit 10`, qwen3.5:4b): 2,8× mais rápido (76 s contra 212 s por questão), conteúdo da 1ª etapa 50% contra 20%; ver `docs/benchmarks/two-stage-routing.md` (2026-10-15)
- [x] Ordenar o processamento local por etapa (flag `STUDAI_STAGE_ORDER`): medido sem ganho (483 s contra 496 s em 10 questões). O código foi removido e fica no commit cf44202; ver `docs/benchmarks/two-stage-routing.md` (commit cf44202, 2026-10-15)
- [x] Pôr o enunciado antes das instruções de cada etapa (flag `STUDAI_QUESTION_FIRST`): medido 26% **mais lento** (85 s contra 68 s por questão, questões 11 a 20). O qwen3.5 é híbrido, e o llama.cpp só retoma o cache em checkpoints a 4 e a ~1 024 tokens do fim do prompt, nunca no fim da questão. Acurácia igual ou melhor (qwen 8 contra 6 de 10 no conteúdo; Gemini 14 contra 14 de 20). O código foi removido e fica no commit f2430e2; ver `docs/benchmarks/two-stage-routing.md` (2026-10-15)
- [x] Testar um modelo local sem camadas recorrentes: `qwen3:4b`, `--limit 10`, 64,5 s contra 74,3 s por questão (13% mais rápido), matéria 80% nos dois, mas conteúdo da 1ª etapa cai de 60% para 40%; não trocar. Ver [`docs/benchmarks/phase4-subject-routing.md`](docs/benchmarks/phase4-subject-routing.md) (commit b1df956, 2026-10-20)
  - [ ] Testar `qwen3:4b` com o enunciado antes das instruções (código do `STUDAI_QUESTION_FIRST` no commit f2430e2), que é onde o cache de prefixo ajudaria
- [x] Melhorar regras de Inglês × Língua Portuguesa × Literatura: prompt `v12-language-rules` mantém a regra original de Inglês e acrescenta o desempate Literatura × Língua Portuguesa; 37/42 contra 36/42 antes (Literatura 12/12), ganho pequeno; taxonomia não mudou de versão para não invalidar as 450 revisões (commits a552b18, 5325f1c, 2026-10-20)
  - [ ] Corrigir à mão 4 revisões aprovadas que põem questões com texto em inglês em Língua Portuguesa/Literatura (IDs no doc do benchmark)
  - [ ] A letra de canção em inglês (Q9 do benchmark) ainda sai como Língua Portuguesa
- [x] Medir recall por matéria: top-15 de 92,7% (qwen3-embedding) e 82,9% (bge-m3), abaixo de 98%, então o pré-filtro **não** foi ativado (2026-10-15)
- [x] Testar o backend Vulkan do Ollama na iGPU AMD: roda sem root numa instância à parte (`OLLAMA_VULKAN=1 OLLAMA_IGPU_ENABLE=1`, porta 11435), 33/33 camadas na GPU, mas 84,5 s contra 74,3 s por questão na CPU; não vale adotar (commit b1df956, 2026-10-20)
- [x] Classificador leve de matéria (regressão logística sobre bge-m3, `subject_model.py`, CLI `train_subject_classifier`), desligado por padrão (`STUDAI_SUBJECT_MODEL=1` liga): 70,2% deixando provas inteiras de fora; com confiança alta responde 14% das questões com 96,9% de acerto; fica desligado (commit a552b18, 2026-10-20)
  - [ ] Reavaliar com o BLUEX e mais revisões humanas; declarar `numpy` no `requirements.txt` se for ativado

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

## Fase 7 — Mapa do conhecimento ("cérebro") e progresso por profundidade

Ideia: um grafo interativo estilo *graph view* do Obsidian, em que cada nó é um nó da taxonomia (matéria → área → conteúdo → subconteúdo) e o progresso mede o quanto você **aprofundou** num setor, não só os acertos em subconteúdos soltos.
Base: `diagnostics/mastery.py` (posterior Beta por nó), `study/service.py` ("Hoje"), `content_taxonomy.json`.

- [x] Visualização em grafo (força-dirigida) da taxonomia: tamanho/brilho do nó = domínio, cor = status (forte/lacuna/incerto/não testado), arestas pai → filho; zoom, arrastar e clicar no nó para ver detalhes e questões — tela "Mapa" em canvas sem dependências (`frontend/knowledge-map.js`): cor = status, tamanho/brilho = profundidade, subconteúdos abrem ao expandir o conteúdo (commit 0708b1d, 2026-10-19)
- [x] Métrica de **profundidade/reforço** por nó: estudar vários subconteúdos de um conteúdo reforça o conteúdo e a área acima dele (ex.: Trigonometria → conteúdo X → subconteúdo Y e Trigonometria → conteúdo Z → subconteúdo W ⇒ Trigonometria recebe reforço e mostra o quanto foi aprofundada) — `diagnostics/depth.py` (commit 0708b1d, 2026-10-19)
  - [x] Definir a fórmula: cobertura (fração de filhos praticados) × domínio dos filhos, propagada para cima, combinada com o posterior Beta já existente — folha: `domínio × e/(e+2)`; nó interno: média dos filhos ponderada pela incidência (piso de 20% para filhos que nunca caíram), o que dá cobertura × domínio dos filhos, mais as respostas rotuladas no próprio nó; o Beta continua sendo o "domínio" (commit 0708b1d, 2026-10-19)
- [x] "O que estudar hoje" prioriza a evolução **de cima para baixo**: primeiro fortalecer conteúdos (amplitude) e só depois descer para subconteúdos, evitando sortear subconteúdos isolados sem consolidar o conteúdo pai — os conteúdos novos são agrupados por conteúdo e entram nesta ordem: lacunas → começados e não consolidados → nunca vistos → consolidados; uma irmã por conteúdo a cada rodada (commit 0708b1d, 2026-10-19)
- [x] API do grafo (nós + arestas + métricas por nó) e tela no frontend — `GET /api/knowledge-map` e `/api/knowledge-map/nodes/{id}` (`reports/knowledge_map.py`); contam também as respostas do "Hoje" (commit 0708b1d, 2026-10-19)
- [x] Testes da propagação do reforço e da ordem de prioridade — `tests/test_diagnostics_depth.py`, `tests/test_study_scheduler.py` (`TopDownTests`), `tests/test_knowledge_map_api.py` (commit 0708b1d, 2026-10-19)

## Fase 8 — Modo foco (tela cheia) para responder questões

Ideia: na tela "Hoje" e em todo lugar onde se respondem questões (Diagnósticos, "Responder prova"), um botão "Modo foco" abre a questão em tela cheia, sem menus nem distrações; você responde e a próxima entra deslizando de lado (carrossel).
Base: `frontend/index.html` (`#today-workspace`, `#diagnostic-workspace`), `frontend/app.js`, `frontend/styles.css`.

- [x] Componente de questão em tela cheia (Fullscreen API + fallback com sobreposição), reutilizável no "Hoje", nos Diagnósticos e no "Responder prova": `frontend/focus-mode.js`; o rascunho do Codex foi substituído (commit 9b58514, 2026-10-20)
- [x] Padronizar a resposta nos três fluxos: selecionar alternativa, clicar em "Responder", informar o motivo quando errar e seguir para a próxima questão (commit f986a40, 2026-10-20)
- [x] Questão ocupando a tela: enunciado, imagens e alternativas grandes e legíveis, com rolagem só quando necessário (inclusive no celular) (commit 9b58514, 2026-10-20)
- [x] Navegação lateral: depois de responder, mostra o feedback e a próxima questão entra deslizando de lado; atalhos de teclado (A–E, Enter, ←/→, Esc para sair) e gesto de deslizar no celular. Nos Diagnósticos a letra só destaca e o Enter confirma, porque o clique já salva (commit 9b58514, 2026-10-20)
- [x] Indicador discreto de progresso (questão X de N) e saída sem perder o que já foi respondido (as respostas continuam sendo salvas no fluxo atual) (commit 9b58514, 2026-10-20)
- [x] Teste manual nas três telas e em largura de celular, no navegador embutido (2026-10-20)
  - [ ] Conferir a Fullscreen API real num Chrome/Firefox normal (o navegador embutido recusou; só a sobreposição foi testada) e o deslizar num celular de verdade

## Fase 9 — Treinamento contínuo

- [x] Identificar “Hoje” como tarefa diária, com progresso e acesso direto ao treinamento
- [x] Reunir a tarefa diária e o treino livre na aba “Treinamento”, com escolha de matérias e quantidade por bloco (até 100 por bloco, sem limite de blocos)
- [x] Priorizar questões com o algoritmo de revisão, lacunas do diagnóstico e conteúdos ainda não praticados; manter prática do acervo quando a fila prioritária se esgotar
- [x] Reutilizar o mesmo registro de respostas, agendamento FSRS e progresso por conteúdo
- [x] Cobrir a extensão da sessão diária para além do plano inicial (`tests/test_study_scheduler.py`)
- [x] Permitir resetar o contexto de aprendizagem sem apagar provas, questões, gabaritos ou configurações

## Processamento de provas

- [x] Mostrar etapas do processamento com progresso granular por página no OCR, por questão nos recortes e na classificação; incluir estado atual e tarefas ativas

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
| 2026-10-19 | Claude / `claude/topic-progress-visualization-bfe261` | Fase 7 adicionada: mapa do conhecimento em grafo e progresso por profundidade | — |
| 2026-10-19 | Claude / `claude/topic-progress-visualization-bfe261` | Fase 8 adicionada: modo foco em tela cheia com navegação lateral entre questões | — |
| 2026-10-19 | Claude / `claude/knowledge-map-depth-progress-5b2ceb` | Fase 7: profundidade por nó (`depth.py`), "Hoje" de cima para baixo, API e tela "Mapa" em grafo | 0708b1d |
| 2026-10-19 | Claude / `claude/fuvest-extrator-2021-741e19` | Fase 3: extrator lê os formatos FUVEST de 1998–2021 (alternativas `a)`, número inline, página inteira, dígitos em imagem); 2021 em bitmap fica para OCR | b7b922b |
| 2026-10-19 | Claude / `claude/peaceful-robinson-bc3448` | Fase 3: OCR (Tesseract) nas provas sem texto legível; 2021 com 90 questões | 5f296ff |
| 2026-10-19 | Claude / `claude/diagnostico-questoes-gabarito-f159ce` | Correção: acervo publicado sem gabaritos (o banco não é versionado) bloqueava o diagnóstico; `gabarito.json` por prova é importado automaticamente e o erro explica as exclusões | 60d5719 |
| 2026-10-20 | Codex / `feature/frontend/answer-confirm-flow` | Fluxo uniforme de resposta: seleção separada do envio, motivo obrigatório para erro e avanço para a próxima questão nos três fluxos | f986a40 |
| 2026-10-20 | Codex / `feature/frontend/answer-confirm-flow` | Tarefa diária reunida com treinamento contínuo; blocos de prática configuráveis por matéria e quantidade, integrados ao FSRS | — |
| 2026-10-20 | Codex / `feature/frontend/answer-confirm-flow` | Botão para zerar progresso e histórico de aprendizagem, mantendo provas, questões e gabaritos | — |
| 2026-10-20 | Codex / `feature/frontend/answer-confirm-flow` | Acompanhamento visual do processamento: etapas e avanço página a página no OCR e questão a questão em recortes/classificação | — |
| 2026-10-20 | Claude (swarm) / `feature/frontend/focus-mode` | Fase 8: modo foco em tela cheia no "Hoje", Diagnósticos e "Responder prova" | 9b58514 |
| 2026-10-20 | Claude (swarm) / `feature/infra/phase4-consolidation` | Fase 4: inventário de chamadas de IA, cache de respostas e embeddings, scraper em `src/`, benchmark offline, ARCHITECTURE.md | 026afd2 |
| 2026-10-20 | Claude (swarm) / `feature/questions/bad-crop-flow` | Fase 3: levantamento de recortes ruins, recorte manual por região, reprocessamento como caso de uso | 0e64891 |
| 2026-10-20 | Claude (swarm) / `feature/questions/ocr-shared-text` | Fase 3: OCR (Tesseract) para 2021, 1997 e 2002 dia 2; correções de recorte do levantamento | c264bac |
| 2026-10-20 | Claude (swarm) / `feature/classification/phase4-subject-rules` | Fase 4: regras Inglês × Português × Literatura (prompt v12), `qwen3:4b` e Vulkan medidos sem ganho, classificador leve de matéria desligado por padrão | 7c74794 |
| 2026-10-22 | Claude / `claude/studai-fuvest-roadmap-57aab2` | Merge com o `main`: ficou o OCR do swarm (1997 e 2002 dia 2 saem com 76–78 questões; o do `peaceful-robinson` achava 0 e 5); o gabarito embutido no acervo foi mantido | — |
| 2026-10-22 | Claude / `claude/studai-fuvest-roadmap-57aab2` | Limpeza: trabalho do Codex (treinamento contínuo, reset, progresso do processamento) commitado e juntado; modo foco adaptado ao botão "Responder" dos Diagnósticos; branches antigas removidas | — |
| 2026-10-22 | Claude / `feature/questions/legacy-ocr` | Fase 3: OCR por coluna, `por+eng`, alternativas lado a lado e 2ª passada `--psm 11`; 2021 com 4 questões incompletas (eram 19) | — |
