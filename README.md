# StudAI

Aplicação local para processar provas, revisar questões e montar diagnósticos de estudo.

> **Primeira instalação / compartilhar o acervo:** siga o [manual plug and play](docs/MANUAL-INSTALACAO.md). O repositório já inclui cinco provas Fuvest processadas e revisadas (450 questões com imagens); é possível abrir e usar esse acervo sem configurar IA. Para processar provas novas, configure Ollama ou Gemini.

O fluxo lê PDFs, extrai questões e imagens e classifica cada questão com um modelo de IA. Cada PDF recebe sua própria pasta de saída para manter simulados separados.

## Preparar o ambiente

Na raiz do repositório, crie e ative o ambiente Python e instale as dependências:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

O Ollama deve estar ativo e ter o modelo configurado no projeto. O padrão atual é `qwen3.5:4b`.

## Processar todos os PDFs

Coloque os PDFs das provas em `simulados/` e os gabaritos em `docs/gabaritos/`. A ferramenta procura provas também nas subpastas, associa gabaritos automaticamente pelo nome/identidade do simulado e importa as respostas no histórico SQLite. Em gabaritos com várias versões, escolhe a versão indicada no nome da prova (por exemplo, `prova-V1` ou `prova-S1`). Itens anulados ficam registrados e não entram no sorteio diagnóstico.

Execute:

```bash
python processar_simulados.py
```

Para usar uma pasta de entrada diferente:

```bash
python processar_simulados.py /caminho/para/os/pdfs
```

As saídas ficam em `simulados/processados/`, preservando subpastas e criando uma pasta para cada nome de PDF:

```text
simulados/
├── simulado2026.pdf
├── 2027/
│   └── prova-s1.pdf
└── processados/
    ├── simulado2026/
    │   ├── questoes.json
    │   ├── questoes.classificado.json
    │   ├── questoes.classificacao.cache.jsonl
    │   └── img/
    └── 2027/
        └── prova-s1/
            ├── questoes.json
            ├── questoes.classificado.json
            ├── questoes.classificacao.cache.jsonl
            └── img/
```

Os PDFs originais permanecem no lugar. A pasta `simulados/processados/` é gerada e ignorada pelo Git.

## Cache de simulados e questões

A classificação usa um cache compartilhado entre CLI e API em `simulados/cache/classificacoes.jsonl`. A chave representa o conteúdo efetivamente enviado à IA (enunciado, alternativas, texto-base e imagens), junto com o modelo, taxonomia e versão do prompt; o número da questão não participa da chave. Assim, provas com as mesmas questões em outra ordem reaproveitam as classificações. Se o PDF e as configurações forem idênticos, a API também reutiliza a extração e o resultado completo do simulado. Para mudar o local do cache, defina `STUDAI_CACHE_DIR`.

Quando não houver uma associação inequívoca pelo nome, o provedor configurado tenta associar usando somente os nomes/identificadores dos arquivos. Se a resposta não passar pela validação de identidade, nada é importado e o lote informa que precisa de revisão. Para uma associação manual de gabarito com várias versões, use `python diagnostico.py importar-gabarito --gabarito docs/gabaritos/arquivo.pdf --questoes simulados/processados/nome/questoes.json --versao V1`.

## Exemplos revisados e classificação local (RAG)

O classificador pode usar exemplos revisados de matéria/tópico como contexto, sem treinar os pesos do modelo. A indexação e a recuperação são locais, via Ollama e SQLite. O padrão usa `simulados/cache/edubench-reviewed-canonical.jsonl` e o modelo de embeddings `qwen3-embedding:0.6b`:

```bash
ollama pull qwen3-embedding:0.6b
PYTHONPATH=src python -m studai.tools.index_classification_examples \
  --base-url http://localhost:11434
```

Para escolher outro conjunto revisado, passe o JSONL como argumento. Linhas sem rótulo válido são ignoradas; matérias e tópicos fora da taxonomia são recusados. Ao indexar novamente o mesmo arquivo, o sistema substitui aquele índice. Depois da indexação, o processamento normal usa RAG automaticamente quando o provedor é Ollama. `--no-rag` desativa a recuperação, e `--embedding-model` seleciona outro modelo já instalado.

O índice armazena texto da questão, identificador, ano, versão, origem e rótulos revisados; gabaritos e respostas esperadas não são enviados no contexto de classificação. O benchmark exclui exemplos do mesmo ano da questão avaliada para evitar contaminação entre treino e validação.

Para revisar questões das provas que você colocou em `simulados/`, exporte os itens processados. A planilha traz matéria/tópico previstos como sugestões e preserva versões, imagens e gabaritos importados; preencha `subject` e `topic` com os rótulos confirmados:

```bash
PYTHONPATH=src python -m studai.tools.review_edubench export-processed \
  --root simulados/processados --output simulados/cache/fuvest-review.csv
PYTHONPATH=src python -m studai.tools.review_edubench import \
  simulados/cache/fuvest-review.csv simulados/cache/fuvest-reviewed.jsonl
PYTHONPATH=src python -m studai.tools.index_classification_examples \
  simulados/cache/fuvest-reviewed.jsonl --base-url http://localhost:11434
```

Para comparar classificação sem exemplos e com RAG, latência, qualidade, workers e threads nos dois Ollama:

```bash
PYTHONPATH=src python -m studai.tools.benchmark_classifier \
  simulados/cache/edubench-reviewed-canonical.jsonl \
  --base-url http://localhost:11434,http://192.168.100.167:11434 \
  --workers 1 --threads 0 --rag both --limit 2 \
  --output simulados/cache/classifier-sweep.json
```

Comece com uma amostra pequena e amplie `--workers`, `--threads` e `--limit` conforme a capacidade das máquinas. Cada servidor deve ter o mesmo modelo de classificação e o embedding instalado. Os relatórios medem acurácia/F1 por matéria e tópico, erros, p95 e questões por minuto, com resultados separados por host.

## Usar dois computadores Ollama

Instale o mesmo modelo nos dois servidores Ollama. Depois passe os dois endereços no comando; use o IP real do segundo computador:

```bash
python processar_simulados.py simulados/ \
  --base-url http://localhost:11434,http://192.168.100.167:11434 \
  --workers 2
```

O classificador distribui os pedidos entre os hosts e exibe o host usado em cada questão. Para controlar os threads de CPU de cada inferência, use `--ollama-threads 8` e ajuste conforme o processador.

## Acervo Fuvest 2017–2026

O processador dedicado consulta os manifestos em `fuvest_acervo/`, seleciona a prova V (ou V1) da 1ª fase, extrai questões e imagens, importa o gabarito correspondente e gera uma planilha para rótulos humanos. Ele não chama a IA nem marca rótulos como revisados:

```sh
PYTHONPATH=src python -m studai.tools.process_fuvest_archive
```

As saídas ficam em `simulados/processados/fuvest-2017-2026/` e `simulados/cache/fuvest-2017-2026-review.csv`, com relatório JSON. Preencha `subject` e `topic` na planilha e importe para um conjunto revisado:

```sh
PYTHONPATH=src python -m studai.tools.review_edubench import \
  simulados/cache/fuvest-2017-2026-review.csv \
  simulados/cache/fuvest-2017-2026-reviewed.jsonl
PYTHONPATH=src python -m studai.tools.index_classification_examples \
  simulados/cache/fuvest-2017-2026-reviewed.jsonl \
  --base-url http://localhost:11434
```

Só os rótulos revisados entram no RAG. O relatório recusa associar gabaritos quando a extração está incompleta. Neste acervo, o extrator não detecta todas as questões de 2017 e 2018 e não encontrou a questão 90 de 2021; confira o relatório antes de usar esses dados. A 2ª fase discursiva não faz parte deste fluxo.

## Interface de benchmark

Abra o painel local para verificar os servidores, conferir modelos carregados e comparar configurações com uma amostra de questões já extraídas:

```bash
python benchmark.py
```

Acesse <http://127.0.0.1:8765>. Informe os endereços Ollama separados por vírgula, escolha o arquivo `questoes.json` e defina a amostra, os workers e os threads a comparar (por exemplo, `1,2` workers e `0,4,8` threads). O painel mostra as requisições em andamento e ordena os resultados por tempo, destacando a configuração mais rápida. Cada questão da amostra é enviada novamente para cada combinação, sem alterar o JSON ou o cache do processamento normal.

O status por servidor usa a API `/api/ps` do Ollama: modelo carregado e memória de GPU reportada, além de requisições ativas observadas pelo painel. O Ollama não informa uso percentual de CPU/RAM por API; para esses números seria necessário instalar um agente de métricas em cada computador. O painel fica restrito a `127.0.0.1` por padrão.

## Diagnóstico inicial por curso

O módulo `diagnostico.py` gera um simulado diagnóstico com questões reais **revisadas**, registra respostas e motivos de erro no SQLite e produz um relatório de estudo. Os perfis ficam em `diagnostico/cursos.json` (v2): Engenharia de Computação EESC/ICMC São Carlos (carreira 308, curso 17), Ciências de Computação ICMC e Engenharia de Computação Poli.

**Pesos.** Com `"formula": "fuvest-2027"`, os pesos vêm da nota final do Guia de Provas 2027, NF = (F1 + D1 + D2)/3: F1 pela incidência histórica de cada matéria nas provas revisadas, D1 = 50% Português (Língua Portuguesa + Literatura) + 50% Redação, D2 = disciplinas de `fase2.dia2` divididas igualmente. Para Eng. Computação São Carlos isso dá ≈ Matemática 25%, Física 25%, Literatura 15%, Língua Portuguesa 12% e Redação 17% (fora do banco de múltipla escolha). Sem `formula`, valem os `pesos` manuais.

**Diagnóstico adaptativo** (padrão, até 30 questões): primeiro uma questão por matéria com orçamento, depois desce área → conteúdo → subconteúdo onde peso do curso × incidência × incerteza é maior; para quando as áreas relevantes das matérias principais ficam definidas ou o orçamento acaba. `--modo static` mantém o sorteio de 51 questões. O domínio de cada nó (matéria → área → conteúdo → folha) é uma posterior Beta com intervalo de 80% (`diagnostics/mastery.py`), e o relatório lista pontos fortes, lacunas e prioridades com critério de saída (`diagnostics/report.py`).

**Relatórios.** A aba "Relatórios" mostra a evolução da nota ponderada e do domínio de cada conteúdo ao longo de diagnósticos e provas respondidas em "Responder prova" (agora salvas no histórico). Para gerar um histórico de teste sem tocar o banco real:

```bash
cp diagnostico/historico.sqlite3 /tmp/studai-sim.sqlite3
PYTHONPATH=src python -m studai.tools.simulate_student --db /tmp/studai-sim.sqlite3 --questions-dir simulados/processados --config diagnostico/cursos.json --course eng-comp-sao-carlos --profile mat-forte-fis-fraca
python api.py --diagnostics-db /tmp/studai-sim.sqlite3
```

### Importar gabarito

Coloque os gabaritos em `docs/gabaritos/` e associe cada gabarito ao JSON classificado correspondente. PDFs precisam ter texto selecionável e linhas de resposta reconhecíveis. Se a leitura não for segura, prepare um JSON no formato `{"1":"A","2":"C"}` e importe esse arquivo. Gabaritos conflitantes ou incompatíveis com as alternativas são recusados.

```bash
python diagnostico.py importar-gabarito \
  --gabarito docs/gabaritos/prova-s1-gabarito.pdf \
  --questoes simulados/processados/prova-s1/questoes.classificado.json
```

### Gerar, responder e consultar

```bash
python diagnostico.py gerar --curso eng-comp-sao-carlos
python diagnostico.py responder ID_DO_DIAGNOSTICO
python diagnostico.py resultado ID_DO_DIAGNOSTICO
python diagnostico.py listar
```

No modo estático o padrão é 51 questões (adaptativo: 30), ajustável com `--quantidade`, e uma semente aleatória fica registrada para reproduzir o sorteio com `--seed`. São 13 categorias porque a taxonomia do classificador separa Literatura de Língua Portuguesa; com os pesos iniciais, o alvo é 3 questões por matéria geral e 9 por matéria específica. O programa usa o provider Ollama do `classifier.py` por padrão; `--provider`, `--model`, `--base-url` e `--api-key-env` permitem reutilizar os demais providers. Cada erro pede um motivo; branco é registrado como `blank`. A análise calcula acertos e motivos em código. Matérias com menos de três respostas ficam `insufficient_data`. O banco local fica em `diagnostico/historico.sqlite3` e é ignorado pelo Git.

A LLM só organiza a cobertura por tópicos disponíveis e interpreta métricas já calculadas; não recebe IDs de questões para selecionar nem define contagens. Se o planejamento temático falhar após correção, o código usa cobertura determinística. Se a análise falhar, os números permanecem salvos e o relatório indica a falha. A seleção usa apenas questões classificadas com gabarito importado; matérias sem cobertura suficiente são sinalizadas.

## Estudo diário: o que estudar hoje

A tela **Hoje** da interface (rotas `GET /api/study/today?courseId=…`, `POST /api/study/today/answers` e `GET /api/study/progress`) monta uma sessão por dia e por curso, com revisão espaçada FSRS (biblioteca `fsrs`, parâmetros padrão, sem passos em minutos: tudo volta em dias). Há dois tipos de cartão: um por conteúdo da taxonomia (o `conteudo_principal_id` da questão) e um por questão errada, para refazer a mesma questão.

A sessão é montada em código, sem IA, nesta ordem e até 15 questões:

1. questões erradas cujo cartão venceu;
2. conteúdos com revisão vencida, primeiro os de menor probabilidade de lembrança e maior peso da matéria no curso;
3. até 5 conteúdos novos: primeiro os vizinhos (mesmo conteúdo) dos pontos fracos do último diagnóstico concluído, depois os demais, distribuídos pelos pesos de `diagnostico/cursos.json`.

As respostas dos diagnósticos entram no agendamento automaticamente, uma única vez cada. Erro pede motivo, como no diagnóstico. A sessão do dia (fuso de São Paulo) fica gravada e não muda ao recarregar. As tabelas `review_cards`, `review_logs`, `study_sessions` e `study_answers` ficam no mesmo `diagnostico/historico.sqlite3`. O código está em `src/studai/features/study/`.

## Executar etapas separadas

Extrair um único PDF:

```bash
python extract.py simulados/simulado2026.pdf -o simulados/processados/simulado2026/
```

Classificar um JSON já extraído:

```bash
python classifier.py simulados/processados/simulado2026/questoes.json
```

## Organização do código

O código principal está em `src/studai/`, como um monólito modular separado em `features/` e `integrations/`. A arquitetura e as regras para contribuir estão em [ARCHITECTURE.md](ARCHITECTURE.md). Os scripts antigos na raiz seguem disponíveis como adaptadores para preservar os comandos atuais. O protótipo Go está isolado em `prototypes/go-pdf-extractor/` e não participa do processamento principal.

## API local e interface web

Inicie `python api.py` e abra <http://127.0.0.1:8787>. Na tela **Processar prova**, selecione até dois modelos Ollama instalados e os modelos Gemini disponíveis. Cada modelo Ollama cria uma faixa de processamento; cada chave Gemini configurada cria outra faixa e alterna pelos modelos selecionados. Questões diferentes são processadas em paralelo. Se uma faixa falhar, a questão tenta as faixas restantes. O progresso identifica qual IA está atendendo cada questão.

Configure as chaves Gemini apenas no `.env` local, que é ignorado pelo Git:

```env
GEMINI_API_KEYS=CHAVE_GEMINI_1,CHAVE_GEMINI_2
STUDAI_GEMINI_PARALLEL_REQUESTS=4
OLLAMA_BASE_URL=http://localhost:11434
```

Cada chave Gemini aceita até quatro chamadas simultâneas por padrão, inclusive quando só há uma chave. Ajuste `STUDAI_GEMINI_PARALLEL_REQUESTS` para um valor de 1 a 16 conforme os limites de taxa da API. O paralelismo não aumenta a cota do projeto; erros de limite de taxa acionam novas tentativas. As variáveis antigas `GEMINI_API_KEY` e `GOOGLE_API_KEY` continuam aceitas. Reinicie `api.py` depois de alterar o `.env`. A interface mostra somente quantas chaves estão configuradas; não exibe seus valores. O catálogo Gemini e os modelos Ollama são consultados pelo backend. A API e a interface são ferramentas de desenvolvimento local e ainda não incluem login.

### Faixas grátis de nuvem (Groq, OpenRouter e Hugging Face)

Além de Ollama e Gemini, o pool aceita faixas compatíveis com a API da OpenAI. Cada chave configurada vira **uma faixa que alterna entre vários modelos**, como no Gemini: cada pedido começa no próximo modelo da lista e, se receber 400, 404, 429 ou erro do servidor, tenta o seguinte. Sem `STUDAI_*_MODELS` no `.env`, Groq e OpenRouter usam os modelos padrão abaixo (catálogos grátis de outubro de 2026; eles mudam com frequência, então sobrescreva no `.env` quando algum sair do ar). O Hugging Face só entra com modelos explícitos, porque a cota grátis do roteador é muito pequena.

| Serviço | Modelos padrão |
|---|---|
| Groq | `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, `openai/gpt-oss-20b` |
| OpenRouter | `nvidia/nemotron-3-super-120b-a12b:free`, `google/gemma-4-31b-it:free`, `google/gemma-4-26b-a4b-it:free` |

```env
GROQ_API_KEY=CHAVE_GROQ
STUDAI_GROQ_MODELS=modelo-1,modelo-2   # opcional; alternados na mesma faixa
STUDAI_GROQ_RPM=30                     # limite por minuto da faixa (0 = sem limite)
STUDAI_GROQ_PARALLEL=2
OPENROUTER_API_KEY=CHAVE_OPENROUTER
STUDAI_OPENROUTER_MODELS=org/modelo:free   # opcional; sufixo :free não consome créditos
STUDAI_OPENROUTER_RPM=20
STUDAI_OPENROUTER_PARALLEL=2
HF_TOKEN=TOKEN_HUGGING_FACE
STUDAI_HF_MODELS=org/modelo:fastest    # roteador https://router.huggingface.co/v1
STUDAI_HF_RPM=0
STUDAI_GEMINI_RPM=0                    # limite por minuto opcional para cada chave Gemini
```

**Cota por modelo no Gemini.** No nível gratuito, cada modelo tem um limite próprio por minuto e por dia. Os Flash têm 5 por minuto e 20 por dia; os Flash Lite têm 15 por minuto e 500 por dia. A rotação do Gemini conta os pedidos de cada modelo:
- Um modelo que chegou ao limite do dia é pulado sem ser chamado.
- Um modelo no limite do minuto passa a vez para o próximo. Se todos estiverem nesse limite, a rotação espera o primeiro liberar, até 65 s.
- Um 429 do Google ensina o mesmo: por minuto, o modelo descansa o tempo que o Google pedir; por dia, ele fica fora até o reset.
- Quando todos os modelos esgotam o dia, a faixa sai do pool até a **meia-noite do Pacífico**, horário em que o Google zera a cota.

A contagem fica em memória e é compartilhada por todos os pools que usam a mesma chave. Depois de reiniciar o servidor, ela recomeça do zero e se ajusta pelos 429. `STUDAI_GEMINI_LIMITS=modelo=rpm/rpd,...` ajusta a tabela, por exemplo depois de ativar o faturamento; `STUDAI_GEMINI_LIMITS=off` desliga a tabela.

Quando uma faixa recebe 429 indicando cota **diária** esgotada, ela sai do pool até a meia-noite local, em vez de ser testada em toda questão. Um 429 por minuto só faz a questão tentar outra faixa. Uploads simultâneos com a mesma configuração compartilham o mesmo pool, para respeitar a capacidade de cada faixa. Envie `useFreeCloud=false` no processamento para ignorar essas faixas.

Erros de conta (401, 402 sem crédito, 403) tiram a faixa do pool. Qualquer outro erro só faz a questão tentar a próxima faixa.

**Nenhuma questão é descartada.** Se uma questão falhar em todas as faixas, ela vai para uma fila de reprocessamento. Ao fim de cada rodada, as questões com falha são processadas de novo depois de 5, 15, 30, 60 e 120 s, dando preferência às faixas que ainda não foram usadas para elas. `STUDAI_CLASSIFICATION_RETRY_ROUNDS` define quantas rodadas extras são feitas (padrão 5; `0` desliga). Só ficam com erro as questões que falharem em todas as rodadas, ou todas as pendentes quando o pool fica sem nenhuma faixa ativa. Na próxima execução, o cache pula o que já deu certo e só essas são refeitas. O resultado registra `tentativas` em cada questão recuperada e `rodadas_reprocessamento` e `questoes_recuperadas` em `processamento_cache`.

### Velocidade da classificação

- **Roteamento hierárquico (padrão).** São 3 chamadas curtas por questão: matéria, conteúdo da matéria e subconteúdo. O prompt de sistema da etapa de conteúdo é fixo por matéria (cerca de 8× menos texto que a árvore inteira), e o Ollama reaproveita parte desse prefixo entre questões. `STUDAI_CLASSIFICATION_ROUTING=flat` volta para a chamada única com a árvore inteira.
- **Cache de prompt no Ollama.** O `qwen3.5:4b` é um modelo híbrido, e o llama.cpp só retoma o cache dele em pontos fixos perto do fim de cada prompt. Por isso reordenar as etapas ou pôr a questão antes das instruções não acelerou nada; os testes estão em `docs/benchmarks/two-stage-routing.md`.
- **Lotes para nuvem.** `STUDAI_CLASSIFY_BATCH=8` classifica até 8 questões só de texto por request em cada etapa, gastando menos da cota diária. Questões que faltarem na resposta seguem individualmente. Use em faixas de nuvem. No Ollama em CPU, o lote não reduz o tempo total.
- **Ollama em CPU.** Use `OLLAMA_NUM_PARALLEL=1` e um worker por host. Com o roteamento hierárquico, `STUDAI_OLLAMA_NUM_CTX=4096` costuma bastar, o que reduz memória e tempo. `STUDAI_OLLAMA_KEEP_ALIVE=30m` evita recarregar o modelo durante lotes longos.
- **GPU integrada AMD (medido, não compensa).** O backend Vulkan roda sem root numa 2ª instância do usuário (`OLLAMA_VULKAN=1 OLLAMA_IGPU_ENABLE=1 OLLAMA_HOST=127.0.0.1:11435`). Com o `qwen3.5:4b` foram 84 s por questão, contra 74 s na CPU. Os passos estão em `docs/benchmarks/phase4-subject-routing.md`.
- **Classificador leve de matéria (desligado).** `STUDAI_SUBJECT_MODEL=1` usa uma regressão logística sobre embeddings bge-m3 (treino: `python -m studai.tools.train_subject_classifier`) no lugar da chamada de matéria quando a confiança é alta. Com 450 revisões, acertou 70% na validação cruzada, ainda abaixo do LLM.
- **Medir.** `python -m studai.tools.evaluate_classifier` registra, por etapa, chamadas, tokens e o tempo de leitura do prompt (`prompt_eval_duration_ns`), que domina em CPU. `python -m studai.tools.benchmark_topic_retrieval --per-subject --model bge-m3` mede o recall dos conteúdos ranqueados por embeddings dentro da matéria.

Para executar os testes de regressão: `python -m unittest discover -s tests`.
