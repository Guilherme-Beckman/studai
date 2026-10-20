# StudAI: manual de instalação e primeiro uso

Este manual prepara uma cópia local do StudAI com o acervo processado que acompanha o repositório. O acervo funciona sem Ollama ou Gemini. Uma IA só é necessária para processar novas provas ou reclassificar questões.

## O que vem pronto

O clone inclui cinco provas Fuvest de primeira fase (2022 K, 2023 K, 2024 K, 2025 V1 e 2026 V1), com 90 questões por prova, imagens extraídas, classificações e estado de revisão humana. As 450 questões estão marcadas como revisadas. Cada prova traz também o gabarito oficial em `gabarito.json`, importado automaticamente no histórico local na primeira vez que a aplicação roda (provas que já têm gabarito no banco não são alteradas). Os dados ficam em `simulados/processados/`, o diretório que a aplicação já consulta.

O histórico de respostas e diagnósticos (`diagnostico/historico.sqlite3`), arquivos enviados, PDFs locais, cache de IA e chaves de API não são distribuídos. Cada instalação começa com histórico de estudo próprio; o acervo de questões é compartilhado pelo repositório.

## Instalação rápida

Requisitos: Python 3.10 ou superior e Git. No Windows, use PowerShell; no macOS/Linux, use o terminal.

```bash
git clone https://github.com/Guilherme-Beckman/studai.git
cd studai
python -m venv .venv
```

Ative o ambiente virtual e instale as dependências:

**Windows PowerShell**

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**macOS/Linux**

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Inicie a aplicação:

```bash
python api.py
```

Abra <http://127.0.0.1:8787>. Vá à área de provas/questões para consultar o acervo e a **Revisão** para ver os estados revisados. O terminal precisa ficar aberto enquanto usar o sistema; `Ctrl+C` encerra o servidor.

## Conectar a IA do seu computador

### Ollama (local)

1. Instale e inicie o Ollama no computador em que o StudAI está rodando.
2. Baixe um modelo de classificação, por exemplo `ollama pull qwen3.5:4b`.
3. Para usar recuperação baseada nas questões revisadas, baixe também `ollama pull qwen3-embedding:0.6b`.
4. Copie `.env.example` para `.env` se quiser alterar o endereço padrão. O endereço padrão é `http://localhost:11434`.
5. Reinicie `python api.py` e selecione o modelo na tela de processamento.

Sem o modelo de embeddings, as questões revisadas continuam disponíveis, mas a recuperação RAG local não poderá ser criada. Na primeira execução que indexar exemplos, o download e a indexação podem demorar.

### Gemini

1. Crie uma chave no Google AI Studio.
2. Copie `.env.example` para `.env` e adicione `GEMINI_API_KEYS=sua-chave`.
3. Salve `.env`, reinicie `python api.py` e selecione Gemini na interface.

Não publique nem envie o arquivo `.env`: ele contém credenciais. O `.env` já está ignorado pelo Git. Cada amigo configura a própria chave. Gemini pode cobrar pelo uso conforme a conta e o modelo selecionado.

## Compartilhar atualizações no acervo revisado

Revisões feitas na interface são locais. Para compartilhar as revisões com outra pessoa, faça commit e push das alterações dentro de `simulados/processados/` junto com o código; o amigo atualiza a cópia com `git pull`. O banco de diagnósticos não deve ser compartilhado para transferir revisões: as revisões das questões ficam nos arquivos `revisao.json` junto a cada prova.

Quando adicionar provas ou imagens, confira o tamanho antes de publicar. O GitHub bloqueia arquivos individuais acima de 100 MB. Não adicione `.env`, `diagnostico/historico.sqlite3`, `simulados/cache/`, PDFs particulares ou `simulados/uploads/`.

## Problemas comuns

- **A página não abre:** confirme que o comando `python api.py` segue ativo e use `http://127.0.0.1:8787`.
- **Não encontra o Python:** instale Python 3.10+ e tente `python3` no macOS/Linux ou `py` no Windows nos comandos de instalação.
- **A interface abre, mas não há modelos Ollama:** inicie o Ollama e confira `http://localhost:11434`; o acervo revisado ainda pode ser consultado sem ele.
- **Alterei `.env`, mas não mudou:** encerre o servidor com `Ctrl+C` e inicie novamente.
- **Clone muito grande/demorado:** o repositório traz o acervo Fuvest e PDFs públicos usados pelo projeto; o primeiro clone pode levar alguns minutos.
