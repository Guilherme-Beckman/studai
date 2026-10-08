# Arquitetura do StudAI

O StudAI é um monólito modular Python: um processo e uma base de código, organizados por funcionalidades. As regras de negócio ficam no pacote `src/studai`; scripts na raiz são apenas adaptadores de compatibilidade para os comandos existentes.

## Mapa do código

- `src/studai/features/questions/`: extração de PDF e processamento em lote.
- `src/studai/features/classification/`: classificação e validação de questões.
- `src/studai/features/answer_keys/`: associação, leitura e importação de gabaritos.
- `src/studai/features/diagnostics/`: sorteio, respostas, métricas e análise diagnóstica.
- `src/studai/integrations/`: provedores de IA e conexão SQLite compartilhados.
- `src/studai/api/`: API HTTP local que converte requisições em chamadas aos casos de uso.
- `src/studai/tools/`: ferramentas locais, como o painel de benchmark.
- `frontend/`: interface web estática que consome a API.
- `prototypes/go-pdf-extractor/`: protótipo Go isolado; não é parte do fluxo principal.

Os dados de entrada e de execução continuam no diretório do serviço: `simulados/`, `docs/`, `diagnostico/` e `.env`. Eles não pertencem ao pacote e não devem ser movidos para `src/`.

## Limites entre módulos

1. Uma funcionalidade pode chamar outra por sua interface pública quando o fluxo do produto exigir isso. Evite importar detalhes internos ou duplicar regras entre funcionalidades.
2. Integrações encapsulam bibliotecas e serviços externos. Casos de uso recebem um provedor ou conexão como dependência em vez de iniciar serviços por conta própria.
3. CLIs e API/web chamam casos de uso; regras de negócio não devem depender de argumentos de terminal, HTTP ou HTML.
4. Formatos JSON existentes são contratos de dados: mudanças devem preservar compatibilidade ou ser acompanhadas por migração explícita.
5. Coloque novos arquivos de dados, cache e banco em diretórios configuráveis fora do pacote.

O cache de classificação fica fora do código, em `simulados/cache/` por padrão (`STUDAI_CACHE_DIR` permite alterar o diretório). Ele é compartilhado por CLI e API e identifica questões pelo conteúdo enviado ao modelo, sem depender do número ou da posição no caderno. A API também mantém a extração e o JSON classificado do PDF para reutilizar o simulado quando arquivo e configuração forem iguais.

## Executar e desenvolver

Instale dependências em `requirements.txt` e execute os comandos existentes no diretório `question-service-pyhton`. Os comandos `extract.py`, `classifier.py`, `processar_simulados.py`, `diagnostico.py` e `benchmark.py` mantêm compatibilidade e delegam ao pacote. Código novo deve importar `studai.*` e permanecer dentro de `src/`.

Para adicionar uma funcionalidade: defina o caso de uso no módulo correspondente, mantenha integrações atrás de interfaces explícitas, exponha-o sem dependência de CLI e adicione testes em `tests/`. A API traduz entrada/saída HTTP e o frontend consome essa API, sem acessar diretamente banco ou arquivos internos.

Inicie a aplicação local com `python api.py`; ela serve a interface e a API na mesma origem, por padrão em `http://127.0.0.1:8787`. Selecione o provedor “Demonstração offline” para testar extração, classificação e diagnóstico sem serviços externos.

## API de desenvolvimento

- `GET /api/health`, `/api/providers`, `/api/exams`, `/api/courses` e `/api/diagnostics` alimentam o painel.
- `POST /api/exams/process?filename=prova.pdf` recebe o PDF no corpo e devolve `jobId`; consulte `GET /api/jobs/{jobId}` até concluir.
- `POST /api/answer-keys/import` recebe `{ "examId": "...", "answers": { "1": "A" } }`.
- `POST /api/diagnostics` gera uma tentativa em background; `GET /api/diagnostics/{id}` mostra progresso e questões sem expor o gabarito.
- `POST /api/diagnostics/{id}/answers` registra `{ "ordinal": 1, "answer": "A", "reason": "calc_error" }`; motivo é exigido para resposta incorreta.
- `POST /api/diagnostics/{id}/finalize` calcula métricas e plano de estudo em background.

As rotas são locais e não têm autenticação nesta fase. Tarefas longas ficam em memória; dados de provas, respostas e diagnósticos continuam nos diretórios persistentes existentes.

## Testes

Execute `python -m unittest discover -s tests` no diretório do serviço. Testes devem usar arquivos temporários e provedores simulados, sem depender de Ollama, chaves ou PDFs do usuário.
