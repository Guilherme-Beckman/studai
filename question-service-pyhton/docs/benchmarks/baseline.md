# Baseline de classificação

Medição registrada em 2026-10-10T01:02:00.085211+00:00.

## Configuração

- Modelo: `qwen3.5:4b`; temperatura 0; saída JSON; contexto Ollama configurado pelo ambiente.
- Endpoints: `http://localhost:11434`, `http://192.168.100.167:11434`; workers=2, no máximo uma chamada por worker.
- Pipeline atual: taxonomia completa no prompt, sem pré-filtro de tópicos, RAG desativado (índice local vazio), texto sem imagens.
- Questões extraídas: 180; questões com rótulo humano utilizável: 41.
- As métricas de acerto usam apenas rótulos humanos. Questões sem esses rótulos medem somente desempenho operacional.

## Resultados

| Métrica | Resultado |
|---|---:|
| Acurácia de matéria (n=41) | 95.1% |
| Acurácia de tópico (n=41) | 46.3% |
| REVISAR | 0/180 (0.0%) |
| Latência média por questão | 20.69 s |
| Latência p50 por questão | 17.01 s |
| Latência p95 por questão | 35.70 s |
| Tokens de entrada por chamada (média informada pelo Ollama) | 9208 |
| Tokens de entrada no total | 1675779 |
| Tempo total de parede | 1865.70 s |
| Chamadas com falha de classificação/JSON | 2 |

### Distribuição por servidor

| Endpoint | Chamadas | Tokens de entrada | Tokens de saída |
|---|---:|---:|---:|
| `http://192.168.100.167:11434` | 121 | 1112957 | 5427 |
| `http://localhost:11434` | 61 | 562822 | 2728 |

### Falhas registradas

- resposta inválida: tópico fora da taxonomia de História: 'Industrialização e padrões industriais'
- resposta inválida: tópico fora da taxonomia de História: 'Conflitos territoriais na América Latina'

## Limitações e arquivos

Há 180 questões extraídas no acervo local, mas somente 41 têm rótulos de matéria e tópico confirmados por revisão humana. Portanto, a medição de acerto ainda é preliminar e não representa todas as matérias/tópicos da taxonomia.
As previsões e métricas completas por questão, incluindo latência, tokens e endpoint, estão em `simulados/cache/baseline-qwen3.5-4b.json` (arquivo local não versionado).
