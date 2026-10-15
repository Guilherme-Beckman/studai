# Roteamento hierárquico vs. chamada única (plano)

Medição: 2026-10-15T20:23:35.739602+00:00 (hierárquico) e 2026-10-15T21:49:48.899445+00:00 (plano).

## Configuração

- Modelo `qwen3.5:4b` no Ollama local, 100% CPU (16 threads), temperatura 0, sem imagens, sem RAG, 1 worker.
- Mesmas 10 primeiras questões de `simulados/cache/reviewed-topic-eval.jsonl` (rótulos humanos até o conteúdo; o subconteúdo ainda não tem gabarito).
- Taxonomia `fuvest-2027.v6`; prompts `v8-hierarchical-routing` e `v7-two-stage-content`.

## Resultados

| Métrica | Hierárquico | Plano |
|---|---:|---:|
| Segundos por questão (média) | 76.4 | 212.3 |
| Segundos por questão (p95) | 109.2 | 359.0 |
| Questões por minuto | 0.79 | 0.28 |
| Tokens de prompt por questão | 3775 | 11369 |
| Leitura do prompt (prefill) por questão, s | 54.2 | 191.4 |
| Chamadas por questão | 3.0 | 2.3 |
| Acurácia de matéria | 80% | 80% |
| Acurácia do conteúdo (1ª etapa) | 50% | 20% |
| Subconteúdo escolhido / pendente | 10 / 0 | 10 / 0 |
| Falhas de JSON/classificação | 0 | 0 |

## Custo por etapa

| Modo | Etapa | Chamadas | Tokens de prompt/chamada | Prefill s/chamada | Geração s/chamada |
|---|---|---:|---:|---:|---:|
| hierarchical | conteudo | 10 | 2050 | 24.6 | 10.6 |
| hierarchical | materia | 10 | 977 | 16.2 | 4.6 |
| hierarchical | subconteudo | 10 | 746 | 13.4 | 5.9 |
| flat | sem_etapa | 13 | 8194 | 140.2 | 11.6 |
| flat | subconteudo | 10 | 716 | 9.2 | 5.1 |

## Questão a questão

| Questão | Gabarito (conteúdo) | Hierárquico | Plano |
|---|---|---|---|
| 1 | `matematica.content.razoes-proporcoes-e-grandezas-proporcionais` | ✗ Matemática → Números, medidas e proporcionalidade → Grandezas compostas e unidades de medida → Grandezas compostas | ✗ Matemática → Números, medidas e proporcionalidade → Grandezas compostas e unidades de medida → Grandezas compostas |
| 2 | `matematica.content.plano-cartesiano-e-distancia-entre-pontos` | ✓ Matemática → Geometria e medidas → Plano cartesiano e distância entre pontos → Distância entre dois pontos | ✓ Matemática → Geometria e medidas → Plano cartesiano e distância entre pontos → Distância entre dois pontos |
| 3 | `fisica.content.impulso-momento-linear-e-colisoes` | ✓ Física → Mecânica → Impulso, momento linear e colisões → Impulso e teorema do impulso | ✗ Física → Mecânica → Movimento circular → Força centrípeta |
| 4 | `fisica.content.efeito-fotoeletrico-e-quantizacao-da-energia` | ✗ Física → Ondas e óptica → Espectro eletromagnético → Relação entre frequência, comprimento de onda e energia | ✗ Física → Energia, tecnologia e ambiente → Efeito estufa e aquecimento global → Mecanismo do efeito estufa |
| 5 | `fisica.content.refracao-da-luz-e-lentes` | ✓ Física → Ondas e óptica → Refração da luz e lentes → Reflexão total e fibras ópticas | ✗ Física → Ondas e óptica → Reflexão da luz e espelhos planos → Leis da reflexão |
| 6 | `fisica.content.cinematica-escalar` | ✓ Física → Mecânica → Cinemática escalar → Movimento uniformemente variado | ✗ Física → Mecânica → Movimento circular → Movimento circular uniforme |
| 7 | `literatura.content.conteudo-de-leitura-obrigatoria` | ✓ Literatura → Gêneros, formas e leitura literária → Conteúdo de leitura obrigatória → Leitura comparada de obras indicadas | ✓ Literatura → Gêneros, formas e leitura literária → Conteúdo de leitura obrigatória → Leitura comparada de obras indicadas |
| 8 | `lingua-portuguesa.content.sentido-denotativo-e-conotativo` | ✗ Língua Portuguesa → Semântica e recursos expressivos → Metáfora e personificação → Antítese e paradoxo | ✗ Literatura → Gêneros, formas e leitura literária → Gêneros literários: conto, crônica, romance e poema → Gênero lírico e versificação |
| 9 | `ingles.content.interpretacao-de-texto-em-ingles` | ✗ Língua Portuguesa → Leitura, interpretação e gêneros → Interpretação de texto: canção → Letra e contexto | ✗ Inglês → Vocabulário e construção de sentido → Expressões idiomáticas → Expressões idiomáticas |
| 10 | `lingua-portuguesa.content.interpretacao-de-texto-cancao` | ✗ Literatura → Gêneros, formas e leitura literária → Gêneros literários: conto, crônica, romance e poema → Gênero lírico e versificação | ✗ Inglês → Vocabulário e construção de sentido → Expressões idiomáticas → Colocações e expressões fixas |

Resultados completos por questão: `simulados/cache/bench-hierarchical-10.json` e `simulados/cache/bench-flat-10.json` (arquivos locais não versionados).


## Pré-filtro por embeddings dentro da matéria

Recall@k dos conteúdos da 1ª etapa, ranqueados por embeddings só dentro da matéria correta (41 questões revisadas):

| Modelo | top-3 | top-5 | top-8 | top-10 | top-15 |
|---|---:|---:|---:|---:|---:|
| `qwen3-embedding:0.6b` | 58.5% | 73.2% | 80.5% | 80.5% | 92.7% |
| `bge-m3` ([BAAI/bge-m3](https://hf.co/BAAI/bge-m3)) | 51.2% | 61.0% | 73.2% | 80.5% | 82.9% |

Detalhes: `subject-retrieval-qwen3-embedding-0.6b.md` e `subject-retrieval-bge-m3.md`.

## Conclusões

- **Velocidade:** o roteamento hierárquico foi 2,8× mais rápido (76 s contra 212 s por questão). O ganho vem quase todo da leitura do prompt: 3,8 mil contra 11,4 mil tokens por questão. Nesta CPU o prefill roda a cerca de 60–70 tokens/s.
- **Acurácia:** a matéria empatou (80%) e o conteúdo da 1ª etapa melhorou (50% contra 20%). Com só 10 questões, isso é um indício, não uma conclusão. O roteamento hierárquico continua como padrão.
- **Cache de prefixo:** na época, os logs do Ollama mostravam `cached n_tokens = 128` em cada chamada. A medição da ordem por etapa (seção abaixo) mostrou que o Ollama 0.40 já reaproveita o prompt de sistema mesmo com as etapas alternadas.
- **Pré-filtro por embeddings: não ativar.** Nenhum modelo chega a 98% de recall. O bge-m3 foi pior que o qwen3-embedding neste conjunto. Cortar opções faria o classificador perder o conteúdo correto em 7% a 17% das questões.
- **Erros recorrentes:** Inglês × Língua Portuguesa × Literatura em questões com canção e poema (questões 8–10). Melhorar as regras e descrições dessas matérias tende a render mais que trocar o modelo.

## Ressalvas

- Amostra pequena (10 questões, 8 matérias ausentes). O benchmark completo de 41 questões e o gabarito de subconteúdo dependem da nova rodada de revisão humana.
- Durante o modo plano, a API local (`api.py`) também estava no ar e pode ter disputado o Ollama, o que infla o tempo de parede. Os tempos de prefill vêm do próprio Ollama.
- No modo plano, as 13 chamadas sem etapa incluem 3 novas tentativas por JSON inválido.


## Ordem por etapa (`STUDAI_STAGE_ORDER=1`)

Medição: 2026-10-15, 18:17–18:33 (horário local). Mesma configuração do roteamento hierárquico acima (10 questões, `qwen3.5:4b` em CPU, 1 worker, Ollama 0.40.0 com runner llama.cpp). As duas rodadas usaram o mesmo código (commit cf44202), uma sem a flag e outra com ela.

Com a flag, `classificar` e `evaluate_classifier` fazem primeiro as 10 chamadas de matéria, depois as de conteúdo agrupadas pela matéria escolhida e por último as de subconteúdo agrupadas pelo conteúdo pai. Cada request continua levando uma questão só.

| Métrica | Questão a questão | Por etapa |
|---|---:|---:|
| Tempo de parede (10 questões), s | 483.0 | 495.8 |
| Segundos por questão (média) | 48.3 | 49.6 |
| Questões por minuto | 1.24 | 1.21 |
| Prefill por questão, s | 29.3 | 31.3 |
| Geração por questão, s | 17.9 | 18.0 |
| Acurácia de matéria / conteúdo (1ª etapa) | 80% / 50% | 80% / 50% |
| Falhas de JSON/classificação | 0 | 0 |

Prefill por chamada (s) e tokens reaproveitados do cache (`cached n_tokens` no `journalctl -u ollama`):

| Etapa | Prompt total | Do cache, nos dois modos | Prefill s/chamada (questão a questão) | Prefill s/chamada (por etapa) |
|---|---:|---:|---:|---:|
| materia | 830–1330 | 309 (o prompt de sistema inteiro) | 11.4 | 12.7 |
| conteudo | 1490–2330 | 604–1305 (o prompt de sistema da matéria) | 16.4 | 18.3 |
| subconteudo | 585–1073 | o prompt inteiro menos 4 tokens | 1.5 | 0.2 |

As predições foram idênticas nos dois modos, questão a questão.

### Conclusões

- **Sem ganho: a flag foi removida** (o código fica no commit cf44202). Os dois modos empataram (a diferença de 2,6% está dentro do ruído). O runner llama.cpp do Ollama 0.40 guarda vários prompts num cache em RAM e procura o de maior prefixo comum (`srv load: looking for better prompt` no log). Por isso o prompt de sistema já vem do cache mesmo com as etapas alternadas. O `cached n_tokens = 128` da medição anterior não se repetiu.
- **O que ainda custa é o texto da questão.** Cada etapa relê o enunciado (cerca de 520 a 1 020 tokens, uns 10 s) depois de um prompt de sistema diferente. Por isso a ordem não importa. Para economizar aqui, a questão teria de vir antes das instruções de cada etapa, de modo que as etapas 2 e 3 reaproveitassem o enunciado. Isso muda os prompts e precisa de um benchmark de acurácia.
- **O subconteúdo saiu quase de graça nas duas rodadas,** porque estas 10 questões já tinham rodado antes e o prompt completo estava no cache. Com questões novas, o subconteúdo volta a custar cerca de 13 s de prefill (ver a tabela de custo por etapa acima). A comparação entre os modos continua justa, mas os tempos absolutos ficaram abaixo dos 76 s da primeira medição por causa disso.

Resultados completos: `bench-stage-off-10.json` e `bench-stage-on-10.json` em `simulados/cache/` (arquivos locais não versionados).


## Enunciado antes das instruções (`STUDAI_QUESTION_FIRST=1`)

Medição: 2026-10-15, 18:43–19:24 (horário local). `qwen3.5:4b` em CPU, 1 worker, Ollama 0.40.0 com runner llama.cpp, `--timeout 300`. Para não pegar prompts já em cache, as rodadas usaram as questões 11 a 20 de `reviewed-topic-eval.jsonl`, e não as 10 primeiras. A API local (`api.py`) estava no ar, mas ociosa.

Com a flag, as 3 etapas mandam o mesmo prompt de sistema curto. A mensagem começa pela questão (com os exemplos do RAG) e termina num bloco `<etapa>` com as instruções e opções da etapa. A ideia era que as etapas 2 e 3 reaproveitassem do cache a leitura da questão. Também medimos uma variante em que o Ollama recebia a questão e o bloco `<etapa>` em duas mensagens do usuário separadas.

| Métrica | Sem a flag | Com a flag | Com a flag, 2 mensagens |
|---|---:|---:|---:|
| Tempo de parede (10 questões), s | 677.2 | 851.4 | 837.5 |
| Segundos por questão (média) | 67.7 | 85.1 | 83.7 |
| Prefill por questão, s | 52.4 | 72.2 | 71.1 |
| Geração por questão, s | 14.2 | 12.5 | 12.1 |
| Acurácia de matéria / conteúdo (1ª etapa) | 100% / 60% | 100% / 80% | 100% / 80% |
| Subconteúdo escolhido / pendente | 10 / 0 | 10 / 0 | 10 / 0 |
| Falhas de JSON/classificação | 0 | 0 | 0 |

Prefill por chamada (s) e tokens reaproveitados do cache (prompt total menos os tokens avaliados no `journalctl -u ollama`; mediana por chamada):

| Etapa | Sem a flag | Com a flag | Com a flag, 2 mensagens |
|---|---:|---:|---:|
| materia | 15.2 s, 0 do cache | 18.4 s, 0 | 17.6 s, 15 |
| conteudo | 26.4 s, 1 158 do cache | 40.8 s, 15 | 40.5 s, 15 |
| subconteudo | 10.8 s, 0 do cache | 13.0 s, 15 | 13.1 s, 15 |

As duas variantes com a flag deram as mesmas predições nas 10 questões. Contra a rodada sem a flag, a matéria coincidiu em 10 e o conteúdo da 1ª etapa em 6.

### Acurácia no Gemini

Para checar só a acurácia com um modelo maior, rodamos as 41 questões com `gemini-3.5-flash-lite` (1 worker), sem e com a flag. A cota gratuita devolveu HTTP 429 em 9 questões sem a flag e em 12 com ela. Nas 20 questões respondidas nas duas rodadas, a matéria acertou 18 contra 19 e o conteúdo da 1ª etapa 14 contra 14. O conteúdo da 1ª etapa coincidiu em 17 das 20. O Gemini não usa o cache do Ollama, então essa rodada não mede tempo.

### Conclusões

- **Sem ganho de tempo: a flag foi removida** (o código fica no commit f2430e2). Com a questão na frente, cada questão ficou 26% mais lenta (85 s contra 68 s). A questão não veio do cache em nenhuma etapa, e o prompt de sistema da etapa de conteúdo, que antes vinha do cache, deixou de vir.
- **Por quê: o qwen3.5 é um modelo híbrido (camadas recorrentes).** O llama.cpp não consegue cortar o cache de um modelo assim num ponto qualquer. Ele só retoma de *checkpoints*, e o log mostra onde eles ficam: a 4 tokens do fim do prompt e a mais 1 020 tokens antes disso (ex.: prompt de 1 524 tokens → checkpoints em 1 520 e 500). A posição depende do tamanho do prompt, não do fim da questão nem da divisão em mensagens, por isso a variante com 2 mensagens também não ajudou. Um prompt só é reaproveitado se algum checkpoint de um prompt anterior cair dentro do prefixo comum.
- **Isso explica as medições anteriores.** O "prompt de sistema do conteúdo vem do cache" acontecia porque o checkpoint a 1 024 tokens do fim de uma chamada de conteúdo caía dentro do prompt de sistema longo da matéria. O subconteúdo "quase de graça" vinha do checkpoint a 4 tokens do fim de um prompt idêntico já rodado. Com o system genérico curto (cerca de 130 tokens), nenhum checkpoint cai no prefixo comum.
- **A acurácia não piorou.** O conteúdo da 1ª etapa acertou 8 contra 6 no qwen e 14 contra 14 no Gemini, mas a amostra é pequena demais para concluir que melhorou.
- **Para reaproveitar a questão,** seria preciso um checkpoint logo no fim da questão. Pela API de chat do Ollama não dá para pedir isso. Os caminhos possíveis são um modelo sem camadas recorrentes (o cache volta a valer para qualquer prefixo) ou o llama.cpp direto, controlando onde ficam os checkpoints.

Resultados completos: `bench-question-first-{off,on,split}-q11-20.json` e `bench-question-first-gemini-{off,on}.json` em `simulados/cache/` (arquivos locais não versionados). A variante com 2 mensagens foi medida e não ficou no código.
