# AGENTS.md — Instruções para Agentes de IA

> Este arquivo é lido automaticamente por agentes de IA compatíveis (Antigravity/Gemini, ChatGPT com Codex/Canvas, Cursor, etc.).  
> Ele define as regras, convenções e fluxo de trabalho para que **múltiplos agentes possam atuar em paralelo** neste repositório sem conflitos.

---

## 1. Visão geral do projeto

**StudAI** é um monólito modular Python que processa provas, classifica questões e gera diagnósticos de estudo.

| Camada | Localização | Responsabilidade |
|---|---|---|
| Regras de negócio | `src/studai/` | Toda lógica de domínio |
| API HTTP | `src/studai/api/` | Tradução HTTP ↔ casos de uso |
| Integrações IA | `src/studai/integrations/` | Provedores de IA, SQLite |
| Features | `src/studai/features/` | questions, classification, answer_keys, diagnostics |
| Frontend | `frontend/` | Interface web estática |
| Scripts raiz | `*.py` na raiz | Compatibilidade retroativa; delegam para `src/` |

**Ponto de entrada:** `python api.py` → `http://127.0.0.1:8787`  
**Testes:** `python -m unittest discover -s tests`

---

## 2. Regras obrigatórias para todos os agentes

Estas regras se aplicam igualmente a qualquer agente (Gemini, ChatGPT, Cursor, etc.):

### 2.1 Escopo de arquivos

- Código novo DEVE ir para `src/studai/` — nunca para a raiz.
- Scripts raiz (`extract.py`, `classifier.py`, etc.) só podem ser editados para corrigir a chamada de delegação. Não adicione lógica neles.
- Dados de execução (`simulados/`, `docs/`, `diagnostico/`, `.env`) nunca devem ser movidos para `src/`.
- Nunca commite arquivos `.env`, arquivos de cache ou PDFs de usuário.

### 2.2 Convenções de código

- Python 3.11+, type hints em funções públicas.
- Casos de uso recebem dependências por injeção (provedor, conexão) — não instanciam serviços diretamente.
- CLIs e rotas HTTP chamam casos de uso; regras de negócio não dependem de HTTP, CLI ou HTML.
- Novos imports: `from studai.<feature>.<module> import ...`

### 2.3 Testes

- Todo novo caso de uso deve ter pelo menos um teste em `tests/`.
- Testes usam arquivos temporários e provedores simulados (mocks). Nunca dependem de Ollama, chaves de API reais ou PDFs do usuário.
- Rode os testes antes de concluir qualquer tarefa: `python -m unittest discover -s tests`

### 2.4 Contratos de dados

- Formatos JSON existentes são contratos. Mudanças de schema devem ser retrocompatíveis ou acompanhadas de migração explícita.
- Cache em `simulados/cache/` — controlado por `STUDAI_CACHE_DIR`. Nunca hardcode caminhos.

---

## 3. Protocolo de trabalho paralelo

Para evitar conflitos quando dois ou mais agentes trabalham simultaneamente:

### 3.1 Divisão de domínios (Work Domains)

Cada agente deve declarar e respeitar um **domínio** de trabalho:

| Domínio | Diretórios/arquivos |
|---|---|
| `questions` | `src/studai/features/questions/` |
| `classification` | `src/studai/features/classification/` |
| `answer_keys` | `src/studai/features/answer_keys/` |
| `diagnostics` | `src/studai/features/diagnostics/` |
| `api` | `src/studai/api/` |
| `integrations` | `src/studai/integrations/` |
| `frontend` | `frontend/` |
| `tests` | `tests/` |
| `infra` | `pyproject.toml`, `requirements.txt`, `.gitignore`, `api.py` |

**Regra:** um agente trabalha em **um domínio por vez**. Se precisar editar fora do seu domínio, anuncie antes (em comentário no PR ou no canal de coordenação).

### 3.2 Fluxo de branch

```
main
 └── feature/<domínio>/<descricao-curta>   ← cada agente usa sua própria branch
```

Exemplos:
- Agente A (Gemini/Antigravity): `feature/classification/cache-invalidation`
- Agente B (ChatGPT): `feature/diagnostics/add-study-plan-metrics`

Nunca commite direto em `main`.

### 3.3 Arquivos compartilhados (alta chance de conflito)

Os arquivos abaixo são editados frequentemente por múltiplos agentes. Antes de modificá-los, verifique se há outro agente trabalhando neles:

- `src/studai/settings.py` — configurações globais
- `src/studai/__init__.py`
- `requirements.txt` / `pyproject.toml`
- `src/studai/integrations/` — provedores de IA compartilhados

**Estratégia:** edições nesses arquivos devem ser pequenas e atômicas. Prefira PR separado exclusivo para eles.

### 3.4 Comunicação entre agentes

Se você (agente) descobrir que sua tarefa afeta o domínio de outro agente:

1. Documente a dependência em um comentário `# TODO(cross-agent): ...` no código.
2. Descreva o impacto no corpo do PR/commit.
3. Não bloqueie — continue sua tarefa; o outro agente irá adaptar.

---

## 4. Checklist antes de encerrar uma tarefa

Qualquer agente deve confirmar todos os itens antes de considerar a tarefa concluída:

- [ ] Código novo está em `src/studai/` (não na raiz)
- [ ] Imports usam `from studai.` (não relativos ao diretório raiz)
- [ ] Não há lógica HTTP/CLI dentro de `features/`
- [ ] Testes adicionados ou atualizados para a funcionalidade
- [ ] `python -m unittest discover -s tests` passou sem erros
- [ ] Nenhum arquivo `.env`, PDF ou cache foi commitado
- [ ] Contratos JSON existentes preservados ou migração documentada
- [ ] Branch criada com o padrão `feature/<domínio>/<descricao>`

---

## 5. Comandos de referência rápida

```bash
# Instalar dependências
pip install -r requirements.txt

# Rodar a aplicação local
python api.py
# Acesse: http://127.0.0.1:8787

# Rodar todos os testes
python -m unittest discover -s tests

# Processar um simulado via CLI (compatibilidade)
python processar_simulados.py

# Variáveis de ambiente relevantes
STUDAI_CACHE_DIR=simulados/cache   # diretório do cache de classificação
```

---

## 6. Padrão para adicionar uma funcionalidade nova

```
1. Crie (ou edite) o caso de uso em:
   src/studai/features/<dominio>/<modulo>.py

2. Se necessário, adicione rota em:
   src/studai/api/

3. Escreva o teste em:
   tests/test_<dominio>_<funcionalidade>.py

4. Exponha via CLI (opcional) editando apenas a chamada de delegação
   no script raiz correspondente.

5. Atualize ARCHITECTURE.md se houver mudança estrutural.
```

---

## 7. O que este arquivo NÃO cobre

- Secrets e credenciais → use `.env` (nunca commite)
- Deploy e CI/CD → documentação futura em `docs/`
- Decisões de produto → abra uma issue no repositório

---

*Última revisão: 2026-10-15. Mantenha este arquivo atualizado quando a arquitetura mudar.*
