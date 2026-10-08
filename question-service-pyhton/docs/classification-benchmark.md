# Benchmark de classificação

O benchmark do classificador consome um JSONL local revisado por pessoas. Cada linha deve seguir o formato:

```json
{"id":"fuvest-2024-f1-q1","year":2024,"exam":"Fuvest","question":"...","alternatives":{"A":"...","B":"..."},"subject":"Biologia","topic":"Genética molecular"}
```

`subject` e `topic` são rótulos de referência e precisam de revisão humana. O resultado do modelo nunca substitui esses rótulos. Execute:

```sh
python -m studai.tools.evaluate_classifier benchmark/data/reviewed.jsonl \
  --provider ollama --model qwen3.5:4b \
  --base-url http://localhost:11434 \
  --source "BLUEX filtrado para USP/Fuvest" --version "<revisão do conjunto>" \
  --output benchmark/results/qwen3.5-4b.json
```

O relatório inclui acurácia e F1 macro para matéria e tópico, falhas de classificação/JSON, tentativas e latência. O contador de tokens fica disponível quando o provedor reporta uso. Execute com os mesmos dados e parâmetros para comparar versões; armazene o conjunto e os relatórios apenas localmente.

Para usar EduBench como ponto de partida da revisão, instale a dependência opcional e gere candidatos USP/Fuvest:

```sh
python -m pip install -e '.[benchmark]'
python -m studai.tools.prepare_edubench \
  --output simulados/cache/edubench-usp-review-candidates.jsonl
```

O loader oficial foi executado neste ambiente: o split contém 3.149 questões e o filtro `exam == "USP"` retorna 1.465 registros nos anos 2015–2025. O campo `suggested_subject` preserva o rótulo amplo da fonte; `subject` e `topic` ficam vazios e precisam de revisão antes de avaliação. Há respostas de referência, diretrizes e dados de imagem no dataset, mas não assuma que as descrições substituem a imagem. BLUEX, por sua vez, fornece rótulos de matéria e imagens, mas não tópicos do StudAI. O cartão/metadado HF consultado não declara uma licença explícita; confirme os termos com os mantenedores antes de redistribuir ou publicar cópias. O preparador mantém a origem no JSONL e o arquivo padrão fica sob `simulados/cache`, que não é versionado.

## Correções revisadas e vetores

`ClassificationCache` expõe armazenamento de exemplos revisados e busca cosseno por vetor. O vetor é serializado em SQLite para manter a interface substituível e evitar nova dependência obrigatória. A inserção deve ser feita somente após confirmação humana; a busca ainda não altera automaticamente o resultado da classificação.

## Cache e Ollama

O cache central fica em `STUDAI_CACHE_DIR/classification-cache.sqlite3` (padrão `simulados/cache`). Os arquivos JSONL antigos são importados uma única vez, sem serem removidos. `STUDAI_OLLAMA_KEEP_ALIVE` controla o tempo de permanência do modelo carregado (padrão `10m`) e `STUDAI_OLLAMA_NUM_CTX` define o contexto (padrão `0`, opção do modelo). `OLLAMA_NUM_PARALLEL` documenta o limite de requisições concorrentes por modelo no servidor. `STUDAI_CPU_THREAD_BUDGET`, quando definido, permite avisar sobre `workers × ollamaThreads` acima do orçamento local.

O cache de classificação é independente do banco de histórico diagnóstico. Uma classificação concluída pode ser reutilizada mesmo se outro job for cancelado; resultados incompletos ou respostas inválidas não são inseridos.
