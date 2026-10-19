# Recall do pré-filtro de tópicos por embeddings

Medição: 2026-10-15T16:45:46.775359+00:00.

## Configuração

- Modelo de embeddings: `qwen3-embedding:0.6b` em `http://localhost:11434`.
- Taxonomia: 477 tópicos; hash `b940d9c1937b6661189263ff8601931bf088a54cc9670daf4715eef1c2ed3cab`.
- Questões revisadas: 41; dataset `simulados/cache/reviewed-topic-eval.jsonl`.
- Cache: `simulados/cache/topic-embeddings.json` (gerado nesta execução).
- O plano previa `bge-m3` ou `nomic-embed-text`. O servidor secundário estava indisponível e o download de Nomic ficou limitado a cerca de 85 KB/s; esta primeira medição usa o modelo Qwen já instalado e deve ser repetida com Nomic antes de escolher o modelo de embeddings.

## Recall

| k | Acertos no top-k | Recall@k |
|---:|---:|---:|
| 5 | 23/41 | 56.1% |
| 10 | 28/41 | 68.3% |
| 15 | 31/41 | 75.6% |
| 20 | 31/41 | 75.6% |

Nenhum k avaliado preserva o tópico correto como candidato em todas as 41 questões. Por isso, o filtro obrigatório fica reprovado nesta configuração e não deve ser ativado: mesmo `k=20` exclui o tópico correto em 10 questões. A próxima tentativa de filtro depende de testar Nomic ou BGE e, depois, medir a acurácia completa do classificador com os candidatos resultantes.

Resultados por questão e candidatos: `docs/benchmarks/topic-retrieval-qwen3-embedding.json`.
