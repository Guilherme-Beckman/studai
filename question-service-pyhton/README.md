# Serviço de extração e classificação de simulados

O fluxo lê PDFs, extrai questões e imagens e classifica cada questão com um modelo de IA. Cada PDF recebe sua própria pasta de saída para manter simulados separados.

## Preparar o ambiente

Na pasta `question-service-pyhton`, crie e ative o ambiente Python e instale as dependências:

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

O módulo `diagnostico.py` gera um simulado diagnóstico com questões reais classificadas, registra respostas e motivos de erro no SQLite e produz um caminho inicial de estudo. O perfil editável de Engenharia de Computação EESC/ICMC (curso 17) está em `diagnostico/cursos.json`: pesos 1 para as matérias gerais e 3 para Física e Matemática. Esses números são um rascunho configurável, não pesos oficiais.

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

O padrão é 51 questões, ajustável com `--quantidade`, e uma semente aleatória fica registrada para reproduzir o sorteio com `--seed`. São 13 categorias porque a taxonomia do classificador separa Literatura de Língua Portuguesa; com os pesos iniciais, o alvo é 3 questões por matéria geral e 9 por matéria específica. O programa usa o provider Ollama do `classifier.py` por padrão; `--provider`, `--model`, `--base-url` e `--api-key-env` permitem reutilizar os demais providers. Cada erro pede um motivo; branco é registrado como `blank`. A análise calcula acertos e motivos em código. Matérias com menos de três respostas ficam `insufficient_data`. O banco local fica em `diagnostico/historico.sqlite3` e é ignorado pelo Git.

A LLM só organiza a cobertura por tópicos disponíveis e interpreta métricas já calculadas; não recebe IDs de questões para selecionar nem define contagens. Se o planejamento temático falhar após correção, o código usa cobertura determinística. Se a análise falhar, os números permanecem salvos e o relatório indica a falha. A seleção usa apenas questões classificadas com gabarito importado; matérias sem cobertura suficiente são sinalizadas.

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

Inicie `python api.py` e abra <http://127.0.0.1:8787>. A interface permite processar uma prova PDF (com gabarito PDF opcional no mesmo formulário), importar gabaritos em JSON e criar/responder diagnósticos. Selecione **Demonstração offline** para exercitar os fluxos sem Ollama ou chaves externas; para classificação real, selecione Ollama e informe o modelo/URL se necessário. A API e a interface são ferramentas de desenvolvimento local e ainda não incluem login.

Com Ollama selecionado, a tela de processamento aceita vários servidores separados por vírgula e configura `Workers` e `Threads por worker`, equivalentes a `--base-url`, `--workers` e `--ollama-threads` do comando em lote. Os valores iniciais são 2 workers e 12 threads.

Para executar os testes de regressão: `python -m unittest discover -s tests`.
