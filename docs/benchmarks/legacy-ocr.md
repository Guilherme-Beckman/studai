# OCR das provas antigas: antes e depois

Medição de 2026-10-22, branch `feature/questions/legacy-ocr`.

## Configuração

- `extract_pdf(pdf, saída fora do repo, dpi=100, full_crops=False)` em todas as provas FUVEST 1ª fase de 1996 a 2021 do `fuvest_acervo/`.
- Tesseract 5 com `por` e `eng` instalados; OCR automático (`ocr="auto"`).
- "Antes" é o `main` em f4a1cba; "depois" é esta branch.
- Colunas: questões extraídas / questões com `alternativas_incompletas` / questões com `enunciado_vazio`.

## O que mudou

1. **OCR por coluna** (`ocr.find_gutter`, `TesseractOcr.__call__`): um render de 50 dpi acha a calha vertical (faixa branca entre 40% e 60% da largura em pelo menos 80% da altura do corpo; um fio no meio da calha não a desfaz). Com calha, cada coluna vai sozinha com `--psm 4` (render com `clip`, coordenadas de volta com `parse_tsv(origin=...)`), e as faixas de largura total acima e abaixo vão com `--psm 3`. Sem calha, a página inteira com `--psm 3`, como antes.
2. **Idioma** `por+eng` quando o inglês está instalado; `STUDAI_OCR_LANG` troca.
3. **Linhas quebradas no vão** (`ocr.split_side_by_side`): "(A) … (D) …" na mesma linha é quebrado na letra do meio, e a alternativa ao lado da caixa "Note e adote" é quebrada no vão largo. O extrator lê a subcoluna esquerda das alternativas antes da direita (`_order_alt_grid`), então o `_ocr_alt` não renomeia mais D para B.
4. **Linha duplicada** em tabela (`ocr.drop_duplicates`): o Tesseract às vezes devolve a mesma linha duas vezes, e a cópia truncada virava outra alternativa (2021 Q28).
5. **Segunda passada `--psm 11`** (`extractor._recover_alternatives`) só no trecho das questões de OCR que ficaram sem alguma letra: a letra que falta entra no lugar das palavras que cobre (2021 Q47: "5)" no lugar de "(C)") ou numa linha nova (2021 Q46, ao lado de frações empilhadas). Letra já lida nunca é trocada.
6. Letra de alternativa do OCR dentro de uma figura (tabela de alternativas, 2021 Q28) fica como texto, não como texto da figura.

## Resultado

| Prova | Antes | Depois |
|---|---|---|
| 2021 | 90 / 19 / 1 | **90 / 4 / 0** |
| 2002 dia 2 | 76 / 13 / 0 | **79 / 9 / 0** |
| 1997 dia 1 | 78 / 24 / 2 | **79 / 17 / 2** |
| 1997 dia 2 | 77 / 28 / 0 | **79 / 16 / 0** |
| 1996 dia 1 (fora de escopo) | 6 / 3 / 0 | 6 / 1 / 0 |
| 1996 dia 2 (fora de escopo) | 4 / 1 / 0 | 3 / 1 / 0 |
| 1998–2001, 2002 dia 1 (camada de texto) | 80 cada | 80 cada, `questoes.json` idêntico byte a byte |
| 2003–2006 (camada de texto) | 100 cada | 100 cada, idêntico |
| 2007–2020 (camada de texto) | 90 cada | 90 cada, idêntico |

2021, as questões citadas na análise:

- **Q2**: antes, as alternativas traziam texto da Q3 ("de cada um dos triângulos e Q a área"); depois, A–E com só os rótulos dos gráficos (as alternativas são figuras).
- **Q47**: antes, só A e D, com "Note e adote" e texto da coluna vizinha dentro; depois, A–E = 5, 10, 50, 100 e 150 cm.
- **Q55**: antes, o enunciado intercalava a Q56 da coluna da direita ("Segundo o texto, conhecer a composição…") e o texto em inglês; depois, enunciado limpo e A–E com as potências de 10.

Ainda incompletas em 2021: Q11 ("(Ajc<", letra colada à fórmula), Q46 (a E, ao lado de fração, lida como "(8)"), Q83 (a linha da C some nas três leituras) e Q90 (alternativas desenhadas).

## Tempo

2021 sozinha, mesma máquina: 52 s antes, 116 s depois. São três chamadas do Tesseract por página (faixa de cima e duas colunas, cada uma carregando `por+eng`) e a segunda passada nas questões incompletas. Só as páginas sem camada de texto pagam isso.

## dpi 300 × 400 nas páginas vetoriais com lixo

| Prova | 300 dpi | 400 dpi |
|---|---|---|
| 2021 | 90 / 4 / 0 | 90 / 2 / 1 |
| 2002 dia 2 | 79 / 7 / 0 | 79 / 7 / 0 |

(Medido antes do ajuste final da segunda passada; 2002 dia 2 saiu com 9 incompletas no resultado final em 300 dpi.)

Fica **300 dpi**. Em 400 dpi o número da Q9 de 2021 se perde: o enunciado e as alternativas da Q9 vão parar na Q10, e o texto da Q10 some. Isso é pior que uma questão incompleta, porque a questão errada passa sem aviso. A qualidade do texto é a mesma: 82,0% dos tokens são palavras limpas em 300 dpi e 81,8% em 400. Em 2002 dia 2 dá empate.

## Fora de escopo

- **1996**: cada questão é uma imagem com "Resposta Correta X" em texto embaixo. É um modelo muito diferente e ficou fora por decisão do usuário (foco em 2010 em diante). As páginas seguem como estavam, quase todas com status `ok` e sem OCR.
- **Passada `--psm 6` para número de questão perdido**: 2021 sai com as 90 questões, então não foi feita. 1997 (79/80) e 2002 dia 2 (79/80) ainda perdem um número cada.
