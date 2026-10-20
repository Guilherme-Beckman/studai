# Inventário das chamadas de IA

Levantamento de 2026-10-20 (Fase 4). Cobre toda chamada a modelo de linguagem ou de embeddings no código de `src/studai/`. Números de tempo e tokens vêm das medições em `docs/benchmarks/` (`qwen3.5:4b` em CPU, Ollama 0.40) e do README; não há custo em dinheiro porque todos os provedores usados são locais ou faixas grátis. Nesses casos o "custo" é tempo de CPU (Ollama) ou cota diária (Gemini, Groq, OpenRouter, Hugging Face).

## Transporte e provedores

Todas as chamadas de texto passam por `Provider.complete`/`complete_json` em `src/studai/integrations/llm.py`, e todo HTTP por `http_post` (até 4 tentativas com espera em 408/409/429/5xx).

| Provedor | Classe | Modelo padrão | Observações |
|---|---|---|---|
| Ollama | `OllamaProvider` | `qwen3.5:4b` (sem "thinking") | Vários hosts; `num_ctx` 16384 por padrão (`STUDAI_OLLAMA_NUM_CTX`); registra tokens e prefill por etapa |
| Gemini | `GeminiProvider` | rotação `gemini-3.8-flash,3.7-flash,3.6-flash` | Cota por modelo no nível grátis: Flash 5/min e 20/dia, Flash Lite 15/min e 500/dia (`ModelQuota`) |
| Groq, OpenRouter, HF, DeepSeek… | `OpenAICompatProvider` | ver tabela do README | Rotação de modelos; RPM por faixa |
| Pool | `RotatingPoolProvider` | faixas acima | Distribui questões entre faixas; tira faixa sem cota até a meia-noite |
| Anthropic | `AnthropicProvider` | informado na CLI | Não usado pela interface |
| Demonstração/Mock | `DemoProvider`, `MockProvider` | — | Heurística offline; não chama rede e não usa cache (`cacheable = False`) |

Embeddings: `ollama_embed` em `src/studai/features/classification/rag.py` (`POST /api/embed`, entrada em lote), modelo `qwen3-embedding:0.6b` (`STUDAI_EMBEDDING_MODEL`).

## Chamadas em produção

| # | Onde (arquivo:função) | Provedor/modelo | Prompt (onde e tamanho) | Quando roda | Cache | Custo medido/estimado |
|---|---|---|---|---|---|---|
| 1 | `features/classification/service.py:classify_routed` → `_ask_json(stage="materia")` | provedor escolhido no upload/CLI | `SUBJECT_TEMPLATE` (fixo) + questão; ~977 tokens | 1× por questão sem cache | `classification-cache.sqlite3`, chave = conteúdo da questão + modelo + taxonomia + prompt + versão do RAG | ~16 s de prefill + 5 s de geração por chamada (CPU) |
| 2 | mesmo, `stage="conteudo"` | idem | `CONTENT_TEMPLATE` com as opções da matéria (fixo por matéria) + questão; ~2 050 tokens | 1× por questão; +1 se o conteúdo não couber e houver matéria secundária | idem (resultado final da questão) | ~25 s de prefill + 11 s de geração; **a etapa mais cara** |
| 3 | `service.py:refine_one` → `_refine_one` (`stage="subconteudo"`) | idem | `REFINE_TEMPLATE` com os filhos do conteúdo + questão; ~750 tokens | quando o conteúdo tem subconteúdos | idem | ~13 s de prefill sem cache de prompt; ~1,5 s quando o Ollama reaproveita |
| 4 | `service.py:classify_one` (modo plano, `STUDAI_CLASSIFICATION_ROUTING=flat`) | idem | `SYSTEM_TEMPLATE` com a árvore inteira; ~8 200 tokens | substitui 1–2; desligado por padrão | idem | ~140 s de prefill por chamada; 2,8× mais lento que o hierárquico |
| 5 | `service.py:classify_batch_routed` → `_ask_batch` (`lote_*`) | faixas de nuvem | mesmos templates + `BATCH_SUFFIX`, até 20 questões por request | só com `STUDAI_CLASSIFY_BATCH>1`, questões só de texto e sem RAG | idem | ~3 requests por grupo em vez de 3N (economia de cota diária) |
| — | Novas tentativas | — | mesma chamada + "Sua resposta anterior foi inválida" | JSON inválido (até 2× por etapa); rodadas de reprocessamento (até 5, `STUDAI_CLASSIFICATION_RETRY_ROUNDS`) | — | legítimas: resposta anterior inutilizável |
| 6 | `features/classification/rag.py:LocalExampleRetriever.retrieve` | Ollama `/api/embed`, `qwen3-embedding:0.6b` | texto da questão (1 entrada) | antes da etapa 1, por questão sem cache, quando o provedor é Ollama e há índice revisado | **novo:** `embedding_vectors` por (modelo, texto) no mesmo SQLite | menor que uma chamada de LLM; antes repetia a cada revisão e a cada rodada de reprocessamento |
| 7 | `rag.py:index_examples` | idem | texto de cada exemplo revisado, lotes de 32 | ao indexar (`index_classification_examples`, ou a API depois de salvar uma revisão) | reaproveita por id/texto; **novo:** também por (modelo, texto) entre conjuntos | 1 request por 32 exemplos novos |
| 8 | `features/diagnostics/service.py:analyze` | provedor do diagnóstico (CLI: `max_tokens=1600`, temperatura 0) | system fixo (~600 caracteres) + JSON com métricas, pesos e matérias; ~1–2 mil tokens | ao concluir uma tentativa (`finalize_attempt`, API e `diagnostico.py`) | **novo:** `ai-responses.sqlite3`, resultado validado por (provedor, prompts) | 1–2 chamadas por tentativa |
| 9 | `features/answer_keys/service.py:associate_answer_key` | provedor do lote (`processar_simulados.py`) | só nomes e identificadores de arquivos; <1 mil tokens | quando o nome da prova não aponta um único gabarito | **novo:** decisão validada em `ai-responses.sqlite3` | até 2 chamadas por prova ambígua **em toda execução do lote** (antes) |
| 10 | `features/diagnostics/service.py:plan_topics` | — | system fixo + tópicos disponíveis | **não é chamado em produção**: `create_attempt` usa a cobertura determinística; só testes e o provedor de demonstração | **novo:** `ai-responses.sqlite3` | 0 |

Chamadas que não são inferência: `api/server.py:_gemini_models_for_key` (lista o catálogo Gemini a cada `/api/providers`, uma por chave, timeout 5 s, sem cache) e `_ollama_models` (`/api/tags`). Não consomem cota de geração.

## Ferramentas de medição (sem cache de propósito)

| Ferramenta | Chamadas |
|---|---|
| `tools/evaluate_classifier.py` | Classifica o conjunto revisado com 1–5 por questão; reenvia tudo para medir tempo e tokens. Usa o retriever (6), que agora reaproveita vetores |
| `tools/benchmark_classifier.py`, `tools/benchmark.py` (painel), API `/api/benchmark` | Reenvia cada questão para cada combinação de workers/threads/RAG |
| `tools/benchmark_processed_exams.py` | Classifica provas processadas com Ollama |
| `tools/benchmark_topic_retrieval.py` | Embeddings de todas as questões e da taxonomia (`TopicEmbeddingIndex`, que guarda a taxonomia em `topic-embeddings.json`) |
| `tools/benchmark_ai_calls.py` | Nenhuma: provedor e embeddings simulados, só conta chamadas |

## Duplicações encontradas

| Duplicação | Situação |
|---|---|
| Vetor da consulta RAG recalculado em cada rodada de reprocessamento e depois de cada revisão | **Removida** (cache por modelo e texto). Medido: 120 embeddings a menos na sequência de `docs/benchmarks/ai-calls-cache.md` |
| Mesmo texto reindexado ao trocar de conjunto revisado (`fuvest-reviewed` × `edubench`) | **Removida** (mesmo cache) |
| Associação de gabarito repetida em toda execução de `processar_simulados.py` | **Removida** (decisão validada guardada; falha de rede não é guardada) |
| Análise diagnóstica repetida ao concluir de novo a mesma tentativa ou métricas idênticas | **Removida** |
| Reclassificar a prova inteira quando o índice RAG muda (a versão do RAG entra na chave do cache) | **Mantida.** Os exemplos mudam o prompt e podem mudar a resposta. É o maior custo repetido: ~3 chamadas de LLM por questão após cada revisão. Opções futuras: chave pelo conjunto de exemplos recuperados para a questão (não pela versão do índice inteiro), ou reclassificar só quando os exemplos recuperados mudarem |
| Etapa 1 e 2 relendo o enunciado (prefill duplicado entre etapas) | **Mantida.** Medido em `two-stage-routing.md`: pôr a questão antes das instruções ficou 26% mais lento no `qwen3.5` (modelo híbrido) |
| Catálogo Gemini consultado a cada `/api/providers` | Fora do escopo (API de outra frente); candidato a cache em memória de alguns minutos |
| Mesmo texto em várias questões da mesma prova | Já coberto pelo cache de classificação por conteúdo |

## Como o cache funciona

- `integrations/ai_cache.py`:
  - `cached_ai_result(kind, provider, system, user, compute)` guarda o resultado já validado em `$STUDAI_CACHE_DIR/ai-responses.sqlite3`. A identidade inclui rótulo do provedor (modelos), temperatura, `max_tokens` e modo JSON. Retorno `None` (falha) nunca é guardado.
  - `EmbeddingCache`/`embed_with_cache` guardam vetores por (modelo, texto). O retriever usa a tabela `embedding_vectors` do mesmo arquivo `classification-cache.sqlite3`.
- Provedores com `cacheable = False` (demonstração, mock) e objetos que não declaram o atributo passam direto.
- `STUDAI_AI_CACHE=off` desliga as duas peças. O cache de classificação continua controlado por `--no-cache`.
- `cache_stats()` devolve acertos e faltas do processo por tipo (`diagnostic_analysis.hit`, `embedding.miss`…).
