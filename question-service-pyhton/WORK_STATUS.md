# WORK_STATUS.md — Estado da taxonomia hierárquica Fuvest

> Atualize este arquivo ao iniciar e concluir as tarefas em branches separadas.
> Roadmap geral do produto e progresso por fase: [`TODO.md`](TODO.md).

## Agente A — Antigravity (Gemini)

- **Branch sugerida:** `feature/classification/fuvest-taxonomy-research` (confirmar com o usuário)
- **Domínio:** pesquisa editorial para `classification`
- **Tarefa:** mapear os programas oficiais Fuvest 2027/2026 para uma árvore de matérias, áreas, conteúdos e subconteúdos, com fontes oficiais e sinônimos úteis. Usar o catálogo de conteúdos existente como insumo, não análises/classificações antigas de provas como autoridade. Não editar arquivos de integração.
- **Status:** `aguardando encaminhamento pelo usuário`
- **Arquivo de entrega:** `docs/fuvest-taxonomy-proposal.json`
- **Arquivos bloqueados:** nenhum; não tocar em `service.py`, `content_taxonomy.json`, API, frontend, diagnósticos, embeddings ou testes.
- **Última atualização:** 2026-10-15T14:08

### Prompt para encaminhar ao Antigravity

> Leia `AGENTS.md` e este `WORK_STATUS.md`. Trabalhe na branch `feature/classification/fuvest-taxonomy-research` no domínio editorial de `classification`. Pesquise o Programa do Vestibular FUVEST 2027 (fonte oficial: https://www.fuvest.br/vestibular-da-usp/ e https://www.fuvest.br/wp-content/uploads/fuvest2027-programa-vestibular.pdf) e confira `docs/fuvest2026_guia-provas-programas.pdf`. Entregue em `docs/fuvest-taxonomy-proposal.json` uma proposta de árvore com as 13 matérias atuais e campos `id`, `name`, `description`, `aliases`, `sources`, `children`; folhas suficientemente específicas para diagnóstico, por exemplo `Matemática → Trigonometria → Identidades trigonométricas → Arco duplo`. Use objetos de conhecimento do programa como fonte canônica; aproveite conceitos do catálogo atual, mas não use classificações antigas de questões como autoridade. Cite URL e seção/página no nó ou ancestral mais específico. Não edite arquivos de integração (`service.py`, API, frontend, diagnósticos, embeddings, testes ou `content_taxonomy.json`). Não faça commit em `main`. Ao terminar, informe branch, contagem de matérias/nós/folhas e limitações de acesso às fontes.

## Agente B — ChatGPT

- **Branch atual:** `feature/classification/hierarchical-taxonomy`
- **Domínio:** `classification` (integração; arquivos separados do trabalho editorial)
- **Tarefa:** tornar a árvore canônica no classificador, revisão, busca/embeddings e diagnósticos; preservar registros antigos sem migração automática; atualizar este status e a documentação.
- **Status:** `implementação inicial concluída; aguardando revisão editorial do Antigravity`
- **Arquivos em edição:** `src/studai/features/classification/taxonomy.py`, `service.py`, `content_taxonomy.json`, integrações de API/diagnóstico/frontend e testes correspondentes.
- **Última atualização:** 2026-10-15

### Entrega inicial do Agente B

- Criada `src/studai/features/classification/content_taxonomy.json` como árvore versionada, sem alterar os rótulos gravados anteriormente.
- Integrados IDs e caminhos ao classificador, revisão, avaliação, embeddings/RAG, API, interface e diagnósticos.
- Suíte completa aprovada: 68 testes (`PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`); `compileall` e `git diff --check` também passaram.
- Próxima etapa editorial: receber `docs/fuvest-taxonomy-proposal.json` do Agente A, auditar fontes e granularidade e então atualizar a árvore sem misturar essa revisão com o código de integração.

## Decisões cross-agent pendentes

| Decisão | Levantada por | Impacto |
|---|---|---|
| Revisar e integrar a proposta editorial do Antigravity quando ela estiver pronta | ChatGPT | conteúdo e fontes da árvore canônica |

## Regra de coordenação

O Antigravity deve limitar suas alterações à árvore editorial. O ChatGPT altera código e testes de integração em sua própria branch. Não mesclar arquivos de integração até revisar as fontes, a estrutura e os IDs da árvore.
