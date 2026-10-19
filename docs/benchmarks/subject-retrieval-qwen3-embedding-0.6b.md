# Recall do pré-filtro por matéria (1ª etapa)

Medição: 2026-10-15T21:53:33.701660+00:00.

## Configuração

- Modelo de embeddings: `qwen3-embedding:0.6b` em `http://localhost:11434`.
- Ranqueia só os conteúdos da 1ª etapa da matéria correta da questão (o roteador hierárquico já escolheu a matéria).
- Questões revisadas: 41; dataset `simulados/cache/reviewed-topic-eval.jsonl`.

## Recall

| k | Acertos no top-k | Recall@k |
|---:|---:|---:|
| 3 | 24/41 | 58.5% |
| 5 | 30/41 | 73.2% |
| 8 | 33/41 | 80.5% |
| 10 | 33/41 | 80.5% |
| 15 | 38/41 | 92.7% |

## Por matéria

| Matéria | Questões | Opções | top-3 | top-5 | top-8 | top-10 | top-15 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Física | 7 | 53 | 6/7 | 7/7 | 7/7 | 7/7 | 7/7 |
| Geografia | 2 | 42 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| História | 2 | 55 | 1/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| Inglês | 1 | 19 | 0/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| Literatura | 4 | 26 | 1/4 | 1/4 | 2/4 | 2/4 | 4/4 |
| Língua Portuguesa | 6 | 36 | 1/6 | 2/6 | 2/6 | 2/6 | 3/6 |
| Matemática | 16 | 49 | 10/16 | 12/16 | 14/16 | 14/16 | 16/16 |
| Química | 3 | 45 | 3/3 | 3/3 | 3/3 | 3/3 | 3/3 |

O filtro só deve cortar opções com o menor k cujo recall seja ≥ 98%; abaixo disso, os candidatos servem apenas para reordenar a lista.

Resultados por questão: `docs/benchmarks/subject-retrieval-qwen3-embedding-0.6b.json`.
