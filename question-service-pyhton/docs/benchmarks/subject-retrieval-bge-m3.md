# Recall do pré-filtro por matéria (1ª etapa)

Medição: 2026-10-15T21:55:13.059742+00:00.

## Configuração

- Modelo de embeddings: `bge-m3` em `http://localhost:11434`.
- Ranqueia só os conteúdos da 1ª etapa da matéria correta da questão (o roteador hierárquico já escolheu a matéria).
- Questões revisadas: 41; dataset `simulados/cache/reviewed-topic-eval.jsonl`.

## Recall

| k | Acertos no top-k | Recall@k |
|---:|---:|---:|
| 3 | 21/41 | 51.2% |
| 5 | 25/41 | 61.0% |
| 8 | 30/41 | 73.2% |
| 10 | 33/41 | 80.5% |
| 15 | 34/41 | 82.9% |

## Por matéria

| Matéria | Questões | Opções | top-3 | top-5 | top-8 | top-10 | top-15 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Física | 7 | 53 | 6/7 | 6/7 | 7/7 | 7/7 | 7/7 |
| Geografia | 2 | 42 | 1/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| História | 2 | 55 | 2/2 | 2/2 | 2/2 | 2/2 | 2/2 |
| Inglês | 1 | 19 | 1/1 | 1/1 | 1/1 | 1/1 | 1/1 |
| Literatura | 4 | 26 | 2/4 | 2/4 | 2/4 | 2/4 | 3/4 |
| Língua Portuguesa | 6 | 36 | 1/6 | 2/6 | 3/6 | 3/6 | 3/6 |
| Matemática | 16 | 49 | 7/16 | 9/16 | 11/16 | 13/16 | 13/16 |
| Química | 3 | 45 | 1/3 | 1/3 | 2/3 | 3/3 | 3/3 |

O filtro só deve cortar opções com o menor k cujo recall seja ≥ 98%; abaixo disso, os candidatos servem apenas para reordenar a lista.

Resultados por questão: `docs/benchmarks/subject-retrieval-bge-m3.json`.
