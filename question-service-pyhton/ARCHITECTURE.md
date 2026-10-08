# Arquitetura do StudAI

O StudAI é um monólito modular Python: um processo e uma base de código, organizados por funcionalidades. As regras de negócio ficam no pacote `src/studai`; scripts na raiz são apenas adaptadores de compatibilidade para os comandos existentes.

## Mapa do código

- `src/studai/features/questions/`: extração de PDF e processamento em lote.
- `src/studai/features/classification/`: classificação e validação de questões.
- `src/studai/features/answer_keys/`: associação, leitura e importação de gabaritos.
- `src/studai/features/diagnostics/`: sorteio, respostas, métricas e análise diagnóstica.
- `src/studai/integrations/`: provedores de IA e conexão SQLite compartilhados.
- `src/studai/tools/`: ferramentas locais, como o painel de benchmark.
- `prototypes/go-pdf-extractor/`: protótipo Go isolado; não é parte do fluxo principal.

Os dados de entrada e de execução continuam no diretório do serviço: `simulados/`, `docs/`, `diagnostico/` e `.env`. Eles não pertencem ao pacote e não devem ser movidos para `src/`.

## Limites entre módulos

1. Uma funcionalidade pode chamar outra por sua interface pública quando o fluxo do produto exigir isso. Evite importar detalhes internos ou duplicar regras entre funcionalidades.
2. Integrações encapsulam bibliotecas e serviços externos. Casos de uso recebem um provedor ou conexão como dependência em vez de iniciar serviços por conta própria.
3. CLIs e, no futuro, API/web chamam casos de uso; regras de negócio não devem depender de argumentos de terminal, HTTP ou HTML.
4. Formatos JSON existentes são contratos de dados: mudanças devem preservar compatibilidade ou ser acompanhadas por migração explícita.
5. Coloque novos arquivos de dados, cache e banco em diretórios configuráveis fora do pacote.

## Executar e desenvolver

Instale dependências em `requirements.txt` e execute os comandos existentes no diretório `question-service-pyhton`. Os comandos `extract.py`, `classifier.py`, `processar_simulados.py`, `diagnostico.py` e `benchmark.py` mantêm compatibilidade e delegam ao pacote. Código novo deve importar `studai.*` e permanecer dentro de `src/`.

Para adicionar uma funcionalidade: defina o caso de uso no módulo correspondente, mantenha integrações atrás de interfaces explícitas, exponha-o sem dependência de CLI e adicione testes em `tests/`. Uma futura API deve chamar os mesmos casos de uso e converter entrada/saída HTTP na camada de API; o frontend deve consumir essa API, sem acessar diretamente banco ou arquivos internos.

## Testes

Execute `python -m unittest discover -s tests` no diretório do serviço. Testes devem usar arquivos temporários e provedores simulados, sem depender de Ollama, chaves ou PDFs do usuário.
