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
- **Cache de prefixo não está funcionando:** os logs do Ollama mostram `cached n_tokens = 128` em cada chamada. O prompt de sistema é relido inteiro em todas as questões, porque etapas diferentes se alternam no único slot. Processar por etapa (todas as matérias, depois os conteúdos agrupados por matéria) deve permitir reaproveitar o prefixo. Precisa ser medido.
- **Pré-filtro por embeddings: não ativar.** Nenhum modelo chega a 98% de recall. O bge-m3 foi pior que o qwen3-embedding neste conjunto. Cortar opções faria o classificador perder o conteúdo correto em 7% a 17% das questões.
- **Erros recorrentes:** Inglês × Língua Portuguesa × Literatura em questões com canção e poema (questões 8–10). Melhorar as regras e descrições dessas matérias tende a render mais que trocar o modelo.

## Ressalvas

- Amostra pequena (10 questões, 8 matérias ausentes). O benchmark completo de 41 questões e o gabarito de subconteúdo dependem da nova rodada de revisão humana.
- Durante o modo plano, a API local (`api.py`) também estava no ar e pode ter disputado o Ollama, o que infla o tempo de parede. Os tempos de prefill vêm do próprio Ollama.
- No modo plano, as 13 chamadas sem etapa incluem 3 novas tentativas por JSON inválido.
