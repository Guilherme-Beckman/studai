# Arquitetura do StudAI

O StudAI é um monólito modular Python: um processo e uma base de código, organizados por funcionalidades. As regras de negócio ficam no pacote `src/studai`; scripts na raiz são apenas adaptadores de compatibilidade para os comandos existentes.

## Mapa do código

```text
api.py, extract.py, classifier.py, processar_simulados.py,   adaptadores da raiz: só ajustam o
diagnostico.py, diagnostico_core.py, benchmark.py,           sys.path e chamam studai.* (mesmos
fuvest_scraper.py                                            argumentos de antes)
src/studai/
├── settings.py              caminhos do serviço e cache_dir() (STUDAI_CACHE_DIR)
├── features/
│   ├── questions/           extractor.py (PDF → questoes.json), batch.py (lote), review.py (revisão humana)
│   ├── classification/      service.py (etapas e driver), taxonomy.py, cache.py (resultados e vetores RAG),
│   │                        rag.py (exemplos revisados), topic_embeddings.py, legacy_mapping.py
│   ├── answer_keys/         service.py: associação, leitura e importação de gabaritos
│   ├── diagnostics/         service.py, mastery.py (Beta por nó), depth.py, courses.py, report.py, cli.py
│   ├── study/               scheduler.py (FSRS) e service.py ("O que estudar hoje")
│   └── reports/             service.py, knowledge_map.py; contrato em CONTRACT.md
├── integrations/
│   ├── llm.py               provedores (Ollama, Gemini, OpenAI-compatíveis, pool, demo) e http_post
│   ├── ai_cache.py          cache de respostas validadas e de vetores de embeddings
│   └── sqlite.py            conexão SQLite compartilhada
├── api/server.py            API HTTP local e arquivos do frontend
└── tools/                   ferramentas de operação e medição (benchmarks, avaliação, indexação RAG,
                             acervo FUVEST, scraper)
frontend/                    interface web estática que consome a API
prototypes/go-pdf-extractor/ protótipo Go isolado; não é parte do fluxo principal
```

| Script da raiz | Módulo | Uso |
|---|---|---|
| `api.py` | `studai.api.server` | API e interface web |
| `extract.py` | `studai.features.questions.extractor` | extrair um PDF |
| `classifier.py` | `studai.features.classification.service` | classificar um `questoes.json` (reexporta o módulo para imports antigos) |
| `processar_simulados.py` | `studai.features.questions.batch` | lote completo |
| `diagnostico.py` | `studai.features.diagnostics.cli` | diagnóstico pela CLI |
| `diagnostico_core.py` | `studai.features.diagnostics.service` | só compatibilidade de import |
| `benchmark.py` | `studai.tools.benchmark` | painel Ollama; `benchmark.py ai-calls` conta chamadas de IA offline |
| `fuvest_scraper.py` | `studai.tools.fuvest_scraper` | baixar o acervo FUVEST (requer `requests` e `beautifulsoup4`) |

`tests/test_modular_boundaries.py` garante que os scripts da raiz não definem funções nem classes.

Os dados de entrada e de execução continuam no diretório do serviço: `simulados/`, `docs/`, `diagnostico/` e `.env`. Eles não pertencem ao pacote e não devem ser movidos para `src/`.

## Limites entre módulos

1. Uma funcionalidade pode chamar outra por sua interface pública quando o fluxo do produto exigir isso. Evite importar detalhes internos ou duplicar regras entre funcionalidades.
2. Integrações encapsulam bibliotecas e serviços externos. Casos de uso recebem um provedor ou conexão como dependência em vez de iniciar serviços por conta própria.
3. CLIs e API/web chamam casos de uso; regras de negócio não devem depender de argumentos de terminal, HTTP ou HTML.
4. Formatos JSON existentes são contratos de dados: mudanças devem preservar compatibilidade ou ser acompanhadas por migração explícita.
5. Coloque novos arquivos de dados, cache e banco em diretórios configuráveis fora do pacote.

## Chamadas de IA e cache

Todas as chamadas de texto passam pela interface `Provider` (`integrations/llm.py`); os embeddings, por `ollama_embed` (`classification/rag.py`). O inventário completo, com prompts, frequência, custo e duplicações, está em [`docs/ai-calls-inventory.md`](docs/ai-calls-inventory.md).

Os caches ficam fora do código, em `simulados/cache/` por padrão (`STUDAI_CACHE_DIR` altera o diretório):

- `classification-cache.sqlite3`: resultado por questão (chave = conteúdo enviado ao modelo, sem número nem posição, + modelo, taxonomia, versão do prompt e do RAG), o índice de exemplos revisados e a tabela `embedding_vectors` (vetor por modelo e texto).
- `ai-responses.sqlite3`: resultados validados das chamadas fora da classificação (análise diagnóstica, associação de gabarito, plano de tópicos), por provedor e prompts exatos.

As regras do cache de IA: só resultados validados são guardados (uma falha volta a ser tentada); provedores offline (`cacheable = False`) não usam cache; `STUDAI_AI_CACHE=off` desliga. A API também mantém a extração e o JSON classificado do PDF para reutilizar o simulado quando arquivo e configuração forem iguais.

## Executar e desenvolver

O repositório tem o próprio serviço StudAI na raiz. Instale dependências em `requirements.txt` e execute `api.py` ou os demais comandos diretamente dali. Os scripts da raiz mantêm compatibilidade e delegam ao pacote (tabela acima). Código novo deve importar `studai.*` e permanecer dentro de `src/`.

Para adicionar uma funcionalidade: defina o caso de uso no módulo correspondente, mantenha integrações atrás de interfaces explícitas, exponha-o sem dependência de CLI e adicione testes em `tests/`. A API traduz entrada/saída HTTP e o frontend consome essa API, sem acessar diretamente banco ou arquivos internos.

Inicie a aplicação local com `python api.py`; ela serve a interface e a API na mesma origem, por padrão em `http://127.0.0.1:8787`. Selecione o provedor “Demonstração offline” para testar extração, classificação e diagnóstico sem serviços externos.

## API de desenvolvimento

- `GET /api/health`, `/api/providers`, `/api/exams`, `/api/courses` e `/api/diagnostics` alimentam o painel.
- `POST /api/exams/process?filename=prova.pdf` recebe o PDF no corpo e devolve `jobId`; consulte `GET /api/jobs/{jobId}` até concluir.
- `POST /api/answer-keys/import` recebe `{ "examId": "...", "answers": { "1": "A" } }`.
- `POST /api/diagnostics` gera uma tentativa em background; `GET /api/diagnostics/{id}` mostra progresso e questões sem expor o gabarito.
- `POST /api/diagnostics/{id}/answers` registra `{ "ordinal": 1, "answer": "A", "reason": "calc_error" }`; motivo é exigido para resposta incorreta.
- `POST /api/diagnostics/{id}/finalize` calcula métricas e plano de estudo em background.
- Revisão de recortes: `POST /api/exams/{id}/questions/{n}/reprocess` (prévia em job) e `.../reprocess/{previewId}/confirm` reextraem uma questão mantendo a revisão (`questions/reprocess.py`); `GET /api/exams/{id}/source/pages[/{p}.png]` e `POST /api/exams/{id}/questions/{n}/manual-crop` fazem o recorte manual por região (`questions/manual_crop.py`); `POST /api/exams/{id}/source` associa o PDF original.
- `GET /api/knowledge-map?courseId=` e `GET /api/knowledge-map/nodes/{id}?courseId=` alimentam a tela "Mapa" (grafo da taxonomia com domínio e profundidade).

As rotas são locais e não têm autenticação nesta fase. Tarefas longas ficam em memória; dados de provas, respostas e diagnósticos continuam nos diretórios persistentes existentes.

## Testes

Execute `python -m unittest discover -s tests` no diretório do serviço. Testes devem usar arquivos temporários e provedores simulados, sem depender de Ollama, chaves ou PDFs do usuário.
