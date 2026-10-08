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

Quando não houver uma associação inequívoca pelo nome, o provedor configurado tenta associar usando somente os nomes/identificadores dos arquivos. Se a resposta não passar pela validação de identidade, nada é importado e o lote informa que precisa de revisão. Para uma associação manual de gabarito com várias versões, use `python diagnostico.py importar-gabarito --gabarito docs/gabaritos/arquivo.pdf --questoes simulados/processados/nome/questoes.json --versao V1`.

## Usar dois computadores Ollama

Instale o mesmo modelo nos dois servidores Ollama. Depois passe os dois endereços no comando; use o IP real do segundo computador:

```bash
python processar_simulados.py simulados/ \
  --base-url http://localhost:11434,http://192.168.1.50:11434 \
  --workers 2
```

O classificador distribui os pedidos entre os hosts e exibe o host usado em cada questão. Para controlar os threads de CPU de cada inferência, use `--ollama-threads 8` e ajuste conforme o processador.

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
