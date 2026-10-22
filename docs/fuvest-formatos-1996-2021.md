# Formatos das provas e gabaritos FUVEST (1ª fase, 1996–2026)

Levantamento feito em 2026-10-22 sobre os PDFs de `fuvest_acervo/`. Medição atual por prova: [`benchmarks/legacy-exams.md`](benchmarks/legacy-exams.md) (`python benchmark.py legacy-exams`).

## Até quando vale o modelo atual

**O modelo de prova atual vale de 2018 a 2026.** De 2007 a 2017 a estrutura é a mesma e só muda o marcador das alternativas. Antes de 2007 muda o número de questões. Antes de 2003 a 1ª fase tinha dois exames.

| Anos | Questões | Marcadores | Versões | Texto do PDF |
|---|---|---|---|---|
| 2022–2026 | 90, 1 dia | número isolado em destaque, alternativas `(A)` | V K Q X Z; V1–V4 a partir de 2025 | camada de texto |
| 2018–2021 | 90, 1 dia | igual ao atual | V K Q X Z | texto, exceto **2021**: fonte Type3 em bitmap, sem Unicode (só OCR) |
| 2007–2017 | 90, 1 dia | alternativas `a)` | V K Q X Z | texto; números desenhados como imagens em 2018 |
| 2003–2006 | 100, 1 dia | alternativas `a)` | V K Q X Z | texto |
| 1998–2002 | dois exames de 80 (`dia_1` = 1º exame, `dia_2` = 2º exame) | número com a sigla: `V.01 –`, `T.77 –` | 1º exame V K Q X Z; 2º exame T M J H P | texto, exceto 2002 2º exame (codificação de fonte trocada: OCR) |
| 1997 | dois dias de 80 | `K.15 –`, `M.01 –` | o acervo tem a prova K (dia 1) e a M (dia 2) | escaneada, ~100 dpi (OCR) |
| 1996 | dois dias, coletânea | uma questão por caixa-imagem, `B.09 -` | — | escaneada; o texto real é só o título e "Resposta Correta X" |

Pontos de atenção:
- A capa do 2º exame de 2001 diz "GRUPO V", mas o gabarito impresso no fim é o da prova T. Quando o gabarito impresso tem uma só versão, ela vale para a prova (`embedded_answer_key`).
- 2019 e 2021 não têm a versão no nome do arquivo. Em 2019 ela aparece no texto ("grupo V"). Em 2021, só no rodapé `PAG 05/28 V`, que precisa de OCR (`detect_exam_variant(..., ocr=...)`).

## Formatos de gabarito

| Formato | Onde | Leitor |
|---|---|---|
| Grade por versão com sigla: `V 01-D   V 46-B` sob `PROVA V  PROVA K …` | `_gab` de 2008–2018; impresso no fim da prova em 1997 (K, M), 1998, 2001, 2003 | `_version_grid` (modo por sigla) |
| Grade sem sigla: `01 – D   51 – B` ou `1  E   46  C` | 2004 (impresso), 2005 p. 2, 2019–2026 | `_version_grid` (modo por coluna) |
| Tabela de correspondência: `RESPOSTA  GRUPO V  GRUPO K …`, uma linha por resposta com o número em cada versão | 1999, 2000, 2005 p. 1, 2006, 2007, `_gab_cor` de 2009–2018, 2002 (impresso), 2022 e 2025 p. 2 (com `PROVA V`) | `_correspondence` |
| `Resposta Correta X` sob cada questão | 1996 | extrator grava `gabarito_impresso`; `printed_answer_key` |

Variações tratadas:
- sigla do grupo na linha de baixo do "GRUPO" (2007, 2016 `_cor`);
- dois blocos lado a lado na mesma linha (1999–2018);
- tabela que continua na página seguinte sem cabeçalho (1999, 2000);
- anulada como `ANULADA`/`Anulada` (2014, 2016), `*` (2022+) ou `54*` (correspondência de 2022);
- hífen Unicode `‐`, travessão `–` e espaço depois do hífen (`V 01‐ D`, 2016);
- gabarito retificado com duas respostas (`48 D E`, 2024): fica a primeira, como antes;
- primeira linha de dados colada no cabeçalho (2025).

Conferência: nos anos com `_gab` e `_gab_cor` (2009–2018), as duas fontes dão as mesmas 90 respostas da versão V. Nos gabaritos que o leitor antigo já lia inteiros (2008–2026), não há nenhuma diferença.

## Gabaritos ausentes no site

O site da FUVEST não publica gabarito separado para 1996–1998 e 2001–2004. Para 1997–2004 ele está na última página da própria prova. O extrator descarta essa página como conteúdo, e `answer_key_pages`/`embedded_answer_key` a leem como gabarito. O app (`/api/exams/process`), o lote (`questions/batch.py`) e `process_fuvest_archive` importam esse gabarito automaticamente quando não há outro.

## Resultado

`python -m studai.tools.process_fuvest_archive --first-year 1997 --last-year 2021`: 31 provas e 2.701 questões, todas com gabarito importado e nenhum erro. Antes, só a faixa 2017–2026 era processada. As provas com OCR (1997, 2002 2º exame) importam o gabarito só das questões extraídas, e os números perdidos ficam listados em `missing_questions`.
