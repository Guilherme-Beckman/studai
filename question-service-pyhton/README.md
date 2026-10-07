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

Coloque os PDFs em `simulados/`. A ferramenta procura PDFs também nas subpastas. Execute:

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

## Usar dois computadores Ollama

Instale o mesmo modelo nos dois servidores Ollama. Depois passe os dois endereços no comando; use o IP real do segundo computador:

```bash
python processar_simulados.py simulados/ \
  --base-url http://localhost:11434,http://192.168.1.50:11434 \
  --workers 2
```

O classificador distribui os pedidos entre os hosts e exibe o host usado em cada questão. Para controlar os threads de CPU de cada inferência, use `--ollama-threads 8` e ajuste conforme o processador.

## Executar etapas separadas

Extrair um único PDF:

```bash
python extract.py simulados/simulado2026.pdf -o simulados/processados/simulado2026/
```

Classificar um JSON já extraído:

```bash
python classifier.py simulados/processados/simulado2026/questoes.json
```
