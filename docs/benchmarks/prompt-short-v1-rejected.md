# Prompt curto v1 — rejeitado

Execução pareada em 2026-10-15 com o mesmo `qwen3.5:4b` e as 41 questões rotuladas. A versão experimental removeu a serialização JSON duplicada da taxonomia, mas manteve os 477 tópicos e o schema estruturado.

## Resultados

| Métrica | Baseline nas mesmas 41 questões | Prompt curto v1 |
|---|---:|---:|
| Acurácia de matéria | 39/41 (95,1%) | 40/41 (97,6%) |
| Acurácia de tópico | 19/41 (46,3%) | 17/41 (41,5%) |
| Média de tokens de entrada/chamada | 9.091 | 4.732 |
| Latência média | 16,86 s | 24,68 s |
| Latência p50 | 13,66 s | 22,22 s |
| Latência p95 | 29,58 s | 35,46 s |
| REVISAR / erros inválidos | 0 / 0 | 0 / 0 |

A latência não é uma comparação controlada: a execução nova usou apenas `localhost`, enquanto o baseline distribuiu chamadas entre dois endpoints; durante parte do experimento o servidor de embeddings também permaneceu carregado. Mesmo com essa limitação, o tópico caiu em duas questões, portanto o prompt experimental foi revertido conforme o critério de não piorar a acurácia.

Os tokens caíram cerca de 48%, confirmando que a taxonomia JSON duplicada aumentava o prompt. Essa economia, sozinha, não compensa a perda observada. Uma nova versão precisa manter ou superar 19/41 tópicos e deve ser comparada com endpoints e carga equivalentes.

Os resultados completos por questão ficam no arquivo local ignorado `simulados/cache/prompt-vs-baseline-qwen3.5-4b.json`; o conjunto de entrada reconstruído fica em `simulados/cache/reviewed-topic-eval.jsonl`.
