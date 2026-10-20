# Fase 4: matéria de Linguagens, modelo sem camadas recorrentes, Vulkan e classificador leve

Medição: 2026-10-20, Ollama 0.40.0 local (CPU de 16 threads, iGPU AMD Barcelo/Renoir `gfx90c`, 22 GB de RAM), temperatura 0, sem imagens, sem RAG, 1 worker. Os resultados completos ficaram em `.scratch/` no worktree e não foram versionados.

Conjuntos usados:

- **Revisões aprovadas:** 450 questões (`write_reviewed_dataset` sobre `simulados/processados`, 5 provas de 2022 a 2026 com 90 questões cada, taxonomia `fuvest-2027.v7`). Por matéria: Matemática 59, Geografia 55, Física 51, Química 49, Biologia 47, História 44, Literatura 40, Língua Portuguesa 36, Inglês 30, Sociologia 18, Filosofia 11, Arte 9, Educação Física 1.
- **Linguagens (45):** 15 questões por matéria (Inglês, Língua Portuguesa e Literatura), sorteadas em passo fixo dessas revisões.
- **10 primeiras** de `reviewed-topic-eval.jsonl`, as mesmas de `two-stage-routing.md`.

## 1. Regras de Inglês × Língua Portuguesa × Literatura

A etapa de matéria do roteamento hierárquico (e o prompt plano) ganhou regras de desempate em `LANGUAGE_SUBJECT_RULES` e descrições mais claras em `DEFAULT_TAXONOMY`. A medição usou `evaluate_classifier --subjects ... --subject-only`, que roda só a chamada de matéria.

Acertos de matéria com `qwen3.5:4b`, nas 42 questões que todas as versões responderam (a rodada antiga perdeu 3 por timeout):

| Prompt | Total | Sem os 2 rótulos disputados | Inglês | Língua Portuguesa | Literatura |
|---|---:|---:|---:|---:|---:|
| `v9` (antes) | 36/42 | 34/40 | 14/15 | 11/15 | 11/12 |
| `v10` (regras longas, 4 itens) | 33/42 | 32/40 | 12/15 | 12/15 | 9/12 |
| `v11` (3 regras curtas, "texto-base em INGLÊS") | 34/42 | 33/40 | 10/15 | 12/15 | 12/12 |
| **`v12` (adotado)** | **37/42** | **36/40** | 13/15 | 12/15 | 12/12 |

Com as 45 questões, o `v12` acertou 40 (89%), em média 17,3 s por questão sem disputa de CPU.

O que ficou no `v12`:

- **Inglês:** a regra antiga ("se a questão exige compreender um texto em inglês...") e a descrição curta antiga. A única mudança é deixar explícito que vale também para poema e letra de canção. As versões que citavam "texto-base em INGLÊS" ou listavam gêneros pioraram o Inglês: o modelo de 4B passou a mandar artigos em inglês com enunciado em português para Língua Portuguesa.
- **Literatura:** é a matéria quando um texto literário em português é lido pelo sentido literário (eu lírico, narrador, personagens, tema da obra, estilo, escola, autor). Isso eliminou os casos Literatura → Língua Portuguesa e Literatura → História.
- **Língua Portuguesa:** é a matéria quando o texto em português serve para cobrar gramática, conectivos, sentido de palavra, figura de linguagem, tipo ou gênero textual, ou quando o texto não é literário (notícia, ensaio, charge, tirinha, quadrinhos).

Erros que continuam no `v12`:

- Inglês → Língua Portuguesa em 2 questões com texto em inglês e pergunta em português (2022 q7, 2024 q6).
- Língua Portuguesa → Literatura em 3 questões com texto literário usado para inferência ou para recursos dos quadrinhos (2026 q87, 2025 q5 e o caso disputado 2025 q42).

Ganho de 1 questão (36 → 37 de 42): é um indício, não uma conclusão.

### Rótulos aprovados que contrariam a regra do Programa

Quatro revisões aprovadas marcam como Língua Portuguesa ou Literatura questões cuja leitura exige um texto em inglês. Isso contraria a regra "texto em inglês → Inglês" e, em dois casos, o rótulo humano de `reviewed-topic-eval.jsonl`. Elas parecem aprovações em lote do rótulo da IA. Vale revisar:

| Questão | Rótulo aprovado | Texto |
|---|---|---|
| `fuvest_2022_primeira_fase_tipo_K-38bf8e1d4c57:q2` | Literatura | poema "Alone", de Maya Angelou, em inglês |
| `fuvest2025_primeira_fase_prova_V1-f512449b47ac:q41` | Literatura | letra de "I hate it here" (Taylor Swift); no conjunto antigo estava como Inglês |
| `fuvest2025_primeira_fase_prova_V1-f512449b47ac:q42` | Língua Portuguesa | mesma letra, em inglês |
| `fuvest2026-fase1-prova-V1-93b417ad6ea7:q58` | Língua Portuguesa | artigo em inglês sobre o telescópio James Webb |

A taxonomia (`content_taxonomy.json`) não mudou. Trocar a versão invalidaria as 450 revisões, porque `diagnostic_ready` exige `taxonomy_version` igual à versão ativa. As regras ficam só nos prompts (`PROMPT_VERSION` = `v12-language-rules`, ou `v10-language-rules` no modo plano), e o cache de classificação é renovado.

## 2. Modelo sem camadas recorrentes: `qwen3:4b`

`ollama pull qwen3:4b` baixou 2,5 GB. O download caiu duas vezes por DNS e terminou na 3ª tentativa. Rodamos `evaluate_classifier --limit 10` com o prompt `v11`, em sequência e sem outra carga, contra o `qwen3.5:4b` nas mesmas condições:

| Métrica | `qwen3.5:4b` (híbrido) | `qwen3:4b` (transformer) |
|---|---:|---:|
| Segundos por questão (média / p95) | 74,3 / 107,2 | **64,5** / 107,5 |
| Questões por minuto | 0,81 | 0,93 |
| Prefill por questão, s | 57,1 | 46,5 |
| Geração por questão, s | 16,2 | 16,6 |
| Tokens de prompt por questão | 4 217 | 4 541 |
| Prefill da etapa de matéria, tokens/s | 70 | 134 |
| Acurácia de matéria | 80% | 80% |
| Acurácia do conteúdo (1ª etapa) | **60%** | 40% |
| Falhas de JSON | 0 | 0 |

- O `qwen3:4b` foi 13% mais rápido. O ganho vem quase todo da etapa de matéria, que tem o prompt de sistema fixo: lá o prefill dobrou de velocidade (o cache volta a valer para qualquer prefixo). Nas etapas de conteúdo e subconteúdo o ganho foi pequeno. Cada etapa ainda tem um prompt de sistema diferente antes da questão, então a questão nunca vem do cache.
- O conteúdo da 1ª etapa caiu de 6 para 4 acertos em 10. O `qwen3:4b` errou o conteúdo de 3 das 4 questões de Física, que o `qwen3.5:4b` acertou.
- **Não trocar o modelo padrão.** Para aproveitar o cache de prefixo de verdade, o próximo teste é o `qwen3:4b` com a questão antes das instruções (flag `STUDAI_QUESTION_FIRST`, código no commit f2430e2). Com um transformer, as etapas 2 e 3 devem reaproveitar a leitura da questão.

## 3. Backend Vulkan na iGPU AMD

Dá para rodar **sem root e sem mexer no serviço do sistema**. O Ollama 0.40.0 já traz `libggml-vulkan.so`, o usuário tem acesso a `/dev/dri/renderD128` (ACL da sessão) e o grupo `ollama` lê os modelos. Uma 2ª instância, só do usuário, sobe numa porta separada:

```bash
OLLAMA_VULKAN=1 OLLAMA_IGPU_ENABLE=1 OLLAMA_HOST=127.0.0.1:11435 \
OLLAMA_MODELS=/usr/share/ollama/.ollama/models OLLAMA_NOPRUNE=1 OLLAMA_NUM_PARALLEL=1 \
  ollama serve
# outro terminal:
PYTHONPATH=src .venv/bin/python -m studai.tools.evaluate_classifier \
  simulados/cache/reviewed-topic-eval.jsonl --model qwen3.5:4b \
  --base-url http://127.0.0.1:11435 --limit 10 --timeout 300
```

- Sem `OLLAMA_IGPU_ENABLE=1`, o Ollama descarta a iGPU ("dropping integrated GPU"). O ROCm também é descartado, porque não há rocBLAS para `gfx90c`.
- Com as duas variáveis, as 33 de 33 camadas do `qwen3.5:4b` foram para `Vulkan0` (RADV RENOIR, 11,7 GiB de memória compartilhada).

| Métrica | CPU (serviço) | Vulkan (iGPU) |
|---|---:|---:|
| Segundos por questão (média / p95) | **74,3** / 107,2 | 84,5 / 111,9 |
| Prefill por questão, s | 57,1 | 57,5 |
| Geração, tokens/s | **9,3** | 5,7 |
| Acurácia de matéria / conteúdo | 80% / 60% | 70% / 60% |

- **Não adotar.** A iGPU leu o prompt na mesma velocidade da CPU e gerou texto mais devagar (a memória é a mesma DDR compartilhada).
- Parte da rodada Vulkan disputou a CPU com outra avaliação de matéria, então os números do Vulkan são um pouco pessimistas. Mesmo assim, nada indica ganho.
- A instância foi encerrada depois da medição. O serviço do sistema não foi alterado.
- Para ligar o Vulkan no serviço, seriam `OLLAMA_VULKAN=1` e `OLLAMA_IGPU_ENABLE=1` em `systemctl edit ollama`, com root. Pelos números acima, não compensa.

## 4. Classificador leve de matéria (regressão logística sobre bge-m3)

O modelo está em `studai.features.classification.subject_model`. Treino e medição: `python -m studai.tools.train_subject_classifier <jsonl...>`. O modelo fica em `STUDAI_CACHE_DIR/subject-model-bge-m3.json`. Ele entra no lugar da chamada de matéria só com `STUDAI_SUBJECT_MODEL=1` e quando a confiança passa de `STUDAI_SUBJECT_MODEL_MIN_CONF` (padrão 0,9). Fica **desligado por padrão**. Para medir no `evaluate_classifier`, use `--subject-model`.

O BLUEX não está disponível localmente, então o treino usou só as 450 revisões aprovadas. Os embeddings bge-m3 das 450 levaram 255 s. A validação cruzada deixou provas inteiras de fora: 5 dobras, uma prova por dobra.

| Configuração | Acurácia geral | Limiar 0,8: cobertura / acerto | Limiar 0,9: cobertura / acerto |
|---|---:|---:|---:|
| Padronizado, L2 0,01 (padrão) | 70,2% | 59% / 83,3% | 39% / 85,9% |
| Padronizado, L2 0,1 | 70,0% | 31% / 90,0% | 14% / 96,9% |
| Sem padronizar (versão inicial) | 70,7% | 0% | 0% |

- Acerto por matéria (padrão): Química 45/49, Matemática 51/59, Geografia 46/55, História 33/44, Física 36/51, Biologia 33/47, Literatura 26/40, Inglês 11/30, Língua Portuguesa 13/36.
- **Recomendação: deixar desligado.** O LLM acerta a matéria em 80% a 100% das questões. O modelo leve só chega perto disso quando responde sozinho em 14% delas, o que economizaria cerca de 14% das chamadas de matéria (uns 2 a 3 s por questão em média). Nas matérias de Linguagens, ele erra mais justamente onde o LLM também erra.
- Vale reavaliar com o BLUEX convertido para o mesmo formato JSONL (o CLI aceita vários arquivos) e com mais revisões humanas de verdade.
