# Benchmark de classificação

## Painel local e revisão

Com `edubench-baseline-100.json` e `edubench-reviewed-canonical.jsonl` em `simulados/cache`, abra o StudAI e escolha **Benchmark e revisão**. O painel mostra métricas, permite filtrar divergências e inspecionar enunciados e textos de apoio. Altere matéria/tópico nos menus e salve; isso atualiza o JSONL e a planilha `edubench-review-canonical.csv`. As métricas são recalculadas imediatamente com os rótulos salvos e as previsões existentes; execute o benchmark novamente para gerar previsões novas.

O botão **Iniciar benchmark** executa o conjunto atual no backend, exibe progresso por questão e permite cancelar depois da inferência atual. A execução atualiza `simulados/cache/edubench-baseline-100.json`; configure o provedor, modelo, URL base, timeout e envio de imagens no painel. Como os arquivos estão em `simulados/cache`, os rótulos e relatórios são locais e ficam fora do Git.

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

Para fazer a revisão em planilha, exporte uma amostra distribuída pelos anos, abra o CSV no LibreOffice/Excel e preencha `subject` e `topic` em cada linha. Use somente matérias da taxonomia do StudAI e mantenha nomes de tópico consistentes (por exemplo, sempre `Funções exponenciais`). O texto da questão e o texto de apoio estão em colunas separadas:

```sh
PYTHONPATH=src python -m studai.tools.review_edubench export \
  simulados/cache/edubench-usp-review-candidates.jsonl \
  simulados/cache/edubench-review.csv --limit 100 --seed 2026
```

Depois de revisar, importe e rode o avaliador:

```sh
PYTHONPATH=src python -m studai.tools.review_edubench import \
  simulados/cache/edubench-review.csv simulados/cache/edubench-reviewed.jsonl
PYTHONPATH=src python -m studai.tools.evaluate_classifier \
  simulados/cache/edubench-reviewed.jsonl --source "EduBench USP revisado" \
  --version "review-1" --output simulados/cache/edubench-baseline.json
```

O importador rejeita linhas sem matéria ou tópico. O avaliador inclui `supporting_texts` no prompt junto à questão e mostra o resultado de cada questão. Antes das 100, faça um smoke test de cinco:

```sh
PYTHONPATH=src python -m studai.tools.evaluate_classifier \
  simulados/cache/edubench-reviewed.jsonl --limit 5 --timeout 60 --http-attempts 1 \
  --provider ollama --model qwen3.5:4b --base-url http://localhost:11434 \
  --source "EduBench USP revisado" --version "review-1-smoke" \
  --output simulados/cache/edubench-smoke.json
```

`--timeout` limita cada inferência e `--http-attempts 1` impede retries longos enquanto se investiga lentidão. Remova `--limit 5` para processar o conjunto inteiro depois que o smoke test terminar normalmente.

## Correções revisadas e vetores

`ClassificationCache` expõe armazenamento de exemplos revisados e busca cosseno por vetor. O vetor é serializado em SQLite para manter a interface substituível e evitar nova dependência obrigatória. A inserção deve ser feita somente após confirmação humana; a busca ainda não altera automaticamente o resultado da classificação.

## Taxonomia fechada de tópicos

`src/studai/features/classification/topic_taxonomy.json` contém a taxonomia por matéria (477 tópicos em 13 matérias), ampliada a partir do guia Fuvest 2026 fornecido para esta revisão. Os 88 tópicos anteriores foram mantidos. A lista de leituras obrigatórias usa uma categoria geral para não ficar presa às obras de um único ano. Alguns tópicos complementam o guia com conteúdos cobrados historicamente; como a parte final de Sociologia do material estava incompleta, esse trecho também recebeu complementação editorial. O classificador pede um tópico dessa lista e valida que pertença à matéria escolhida. Se não houver opção adequada, devolve `REVISAR` com confiança reduzida e marca a questão para revisão. É possível fornecer outra taxonomia pelo CLI com `--topicos arquivo.json`. Mudanças na taxonomia invalidam resultados antigos do cache. A matéria aparece como `Arte`; diagnósticos ainda aceitam dados legados que usam `Artes`.

## Cache e Ollama

O cache central fica em `STUDAI_CACHE_DIR/classification-cache.sqlite3` (padrão `simulados/cache`). Os arquivos JSONL antigos são importados uma única vez, sem serem removidos. `STUDAI_OLLAMA_KEEP_ALIVE` controla o tempo de permanência do modelo carregado (padrão `10m`) e `STUDAI_OLLAMA_NUM_CTX` define o contexto (padrão `16384`; use `0` para deixar o Ollama escolher). O contexto maior evita erros em questões longas e usa mais memória durante a inferência. `OLLAMA_NUM_PARALLEL` documenta o limite de requisições concorrentes por modelo no servidor. `STUDAI_CPU_THREAD_BUDGET`, quando definido, permite avisar sobre `workers × ollamaThreads` acima do orçamento local.

O cache de classificação é independente do banco de histórico diagnóstico. Uma classificação concluída pode ser reutilizada mesmo se outro job for cancelado; resultados incompletos ou respostas inválidas não são inseridos.

## RAG local com exemplos revisados

Os rótulos humanos do JSONL podem ser indexados em `classification-cache.sqlite3` como exemplos para o classificador. A busca usa embeddings do Ollama e o texto das questões; matéria/tópico revisados são anexados ao prompt. Respostas de gabarito e diretrizes ficam no acervo, mas não entram nesse contexto. Instale o modelo de embeddings em cada Ollama usado pelo classificador e gere o índice:

```sh
ollama pull qwen3-embedding:0.6b
PYTHONPATH=src python -m studai.tools.index_classification_examples \
  simulados/cache/edubench-reviewed-canonical.jsonl \
  --base-url http://localhost:11434
```

O comando valida matéria e tópico contra a taxonomia antes de gravar. Uma indexação seguinte substitui apenas os vetores daquele arquivo. O processamento normal usa RAG automaticamente se houver índice para o modelo configurado; `classifier.py --no-rag` executa o baseline sem exemplos. Use `--embedding-model` para mudar o modelo local. As mudanças no corpus entram na chave do cache de classificação.

Para uma comparação A/B, rode o benchmark numa amostra curta nos dois computadores e aumente o limite depois de validar a configuração:

```sh
PYTHONPATH=src python -m studai.tools.benchmark_classifier \
  simulados/cache/edubench-reviewed-canonical.jsonl \
  --base-url http://localhost:11434,http://192.168.100.167:11434 \
  --workers 1 --threads 0 --rag both --limit 2 \
  --output simulados/cache/classifier-sweep.json
```

O avaliador RAG exclui do índice as questões do mesmo ano da amostra avaliada. O relatório inclui acurácia e F1 de matéria/tópico, falhas, mediana, p95, vazão e métricas por host. Expanda os workers/threads e a amostra depois do smoke test. Cada configuração RAG é comparada à mesma configuração sem RAG; o relatório destaca a mais rápida que preservou acurácia e F1 nas duas dimensões. O cache vetorial é apenas local e não contém as chaves de resposta no contexto de classificação.
