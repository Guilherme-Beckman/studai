# Chamadas de IA antes e depois do cache de IA

Medição: 2026-10-20. Ferramenta: `python benchmark.py ai-calls` (`studai.tools.benchmark_ai_calls`).

## Como foi medido

- Tudo offline: um provedor Ollama simulado responde cada etapa pelo schema pedido, e os embeddings também são simulados. O script **conta chamadas**; não mede tempo nem acurácia de modelo real.
- `STUDAI_CACHE_DIR` aponta para um diretório temporário, apagado no fim. O cache real não é lido nem alterado.
- Prova sintética de 30 questões só de texto (6 matérias), RAG ativo com 12 exemplos revisados, `workers=4`, roteamento hierárquico (3 chamadas por questão).
- "Antes" é o commit `12b9e63` (main antes desta fase), com o mesmo script copiado para uma cópia de `src/`. "Depois" é a branch `feature/infra/phase4-consolidation`.

Os cenários rodam em sequência, sobre o mesmo cache:

| Cenário | O que acontece |
|---|---|
| `indice_rag` | Indexa 12 exemplos revisados |
| `classificacao_inicial` | 1ª classificação da prova |
| `reprocessar_igual` | Mesma prova, mesma configuração |
| `indice_rag_alterado` | Uma revisão nova entra no índice (13 exemplos) |
| `reprocessar_apos_revisao` | Mesma prova de novo; a versão do RAG mudou, então o cache de classificação não vale |
| `reprocessar_com_falha` | Índice muda de novo; a 1ª chamada de cada questão falha (503) e a questão volta na rodada de reprocessamento |
| `gabarito_lote_2x` | Associação ambígua de gabarito em duas execuções do lote |
| `analise_diagnostica_2x` | Concluir a mesma tentativa de diagnóstico duas vezes |

## Resultados

| Cenário | LLM antes | LLM depois | Textos embedados antes | Textos embedados depois |
|---|---:|---:|---:|---:|
| `indice_rag` | 0 | 0 | 12 | 12 |
| `classificacao_inicial` | 90 | 90 | 30 | 30 |
| `reprocessar_igual` | 0 | 0 | 0 | 0 |
| `indice_rag_alterado` | 0 | 0 | 1 | 1 |
| `reprocessar_apos_revisao` | 90 | 90 | 30 | **0** |
| `reprocessar_com_falha` | 120 | 120 | 60 | **0** |
| `gabarito_lote_2x` | 2 | **1** | 0 | 0 |
| `analise_diagnostica_2x` | 2 | **1** | 0 | 0 |

No total, o "depois" fez 120 embeddings de consulta a menos (de 133 para 43) e 2 chamadas de LLM a menos nesta sequência. As chamadas de LLM da classificação não mudaram: o cache por questão (`classification-cache.sqlite3`) já cobria a reexecução igual.

## Leitura

- **Embeddings do RAG.** Antes, cada questão sem cache de classificação pedia o vetor da consulta ao Ollama de novo: depois de cada revisão (o índice muda e com ele a chave do cache de classificação) e em cada rodada de reprocessamento. Agora o vetor fica em `embedding_vectors`, no mesmo arquivo SQLite do cache de classificação, por (modelo, texto). Na rodada com falha, o "antes" embedou cada questão duas vezes (30 na 1ª rodada + 30 na 2ª).
- **Custo real.** Com `qwen3-embedding:0.6b` em CPU, uma consulta custa bem menos que uma chamada de classificação (a etapa de matéria leva cerca de 16 s de prefill; ver `two-stage-routing.md`), então o ganho em tempo é pequeno por questão. O ganho é tirar o Ollama de embeddings do caminho em reprocessamentos, inclusive quando o host de embeddings está fora do ar.
- **O que continua caro.** Reprocessar depois de uma revisão ainda refaz as 3 chamadas de LLM por questão, porque a chave do cache de classificação inclui a versão do índice RAG (os exemplos mudam o prompt). É o maior custo repetido que sobrou; ver o inventário em `docs/ai-calls-inventory.md`.
- **Gabarito e análise.** Uma chamada a menos por execução repetida. Em `processar_simulados.py`, cada prova sem gabarito inequívoco pelo nome consultava a IA em **toda** execução do lote (até 2 chamadas); agora a decisão validada fica guardada até a lista de gabaritos mudar.

## Prova real (sanidade)

O mesmo script com `--exam` numa prova processada local de 90 questões (pasta `simulados/processados/web/…`, não versionada): 21 chamadas de LLM na 1ª classificação e 83 questões vindas do cache já na 1ª rodada. A extração dessa prova gerou textos repetidos (questões quase só com imagem), e o cache por conteúdo une questões com o mesmo texto. Por isso usamos a prova sintética para comparar.

## Reproduzir

```bash
python benchmark.py ai-calls                      # prova sintética de 30 questões
python benchmark.py ai-calls --exam caminho/questoes.json --json saida.json
```
