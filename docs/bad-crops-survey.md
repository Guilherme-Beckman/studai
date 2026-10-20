# Levantamento de recortes ruins (Fase 3)

Data: 2026-10-20. Base: as 6 provas processadas em `simulados/processados/web/` (FUVEST 1ª fase 2021, 2022 K, 2023 K, 2024 K, 2025 V1 e 2026 V1; 540 questões) e os PDFs do `fuvest_acervo/`.

## Estado das revisões

- 2022 a 2026: as 450 questões estão aprovadas (`reviewed`) em `revisao.json`. Nenhuma está marcada como `bad_crop` ou `unusable`, nenhuma tem `edits` nem observação.
- 2021: ainda sem `revisao.json`.

A fila `GET /api/review/bad-crops` está vazia. Por isso este levantamento não pôde partir das marcações humanas. Ele usou quatro sinais:

1. as sinalizações da extração (`revisar`);
2. o enunciado vazio;
3. o enunciado que cita uma imagem sem que a questão tenha figura;
4. as imagens do PDF que nenhuma questão da página recebeu (`page.get_image_info()` contra `figuras_enunciado`, figuras das alternativas e figuras dos textos-base).

Depois os casos foram conferidos olhando o recorte e a página.

**Achado importante:** três das questões problemáticas abaixo foram aprovadas na revisão (2023 Q28, 2025 Q1 e 2026 Q42). A revisão aprova pelo texto e pela classificação, e o recorte ruim passa despercebido.

## Casos reais

| Prova | Questão | Pág. | Causa | Sintoma |
|---|---|---|---|---|
| 2025 V1 | 1 | 2 | Imagens laterais + faixa de página inteira | A foto do manto tupinambá, na coluna direita ao lado dos textos 1 e 2, não virou figura (`figuras_enunciado` vazio), embora o enunciado diga "com base nos textos e na imagem". O comando e as alternativas ocupam a largura da página, e o recorte `q01_completa1.png` tem a largura de uma coluna, então corta o fim das linhas. O texto extraído está completo. |
| 2023 K | 28 | 10 | Outro: enunciado lido como figura | O enunciado ("Na frase … 'convey' …") virou `q28_fig1.png`, o campo `enunciado` ficou vazio e a alternativa A ganhou uma figura espúria. Não houve sinalização `enunciado_vazio`, porque a questão tem figura. Ela faz parte do texto-base compartilhado 27–29. |
| 2026 V1 | 42 | 19 | Outro: enunciado lido como figura | O texto-base, o comando e as alternativas A–C viraram `q42_fig1.png`. O enunciado ficou vazio e as alternativas A–C trazem só a primeira linha. Sem sinalização. Na página, a busca por "espetáculos circenses" não acha o trecho, ou seja, a camada de texto desse bloco não está em Unicode legível. |
| 2024 K | 90 | 34 | Outro: rodapé da página | A alternativa E saiu como "0,48 CONSIDERADO NA RASCUNHO NÃO SERÁ CORREÇÃO", pois o aviso de rascunho do rodapé foi lido em colunas e colado à última alternativa. O recorte saiu em 3 partes. |
| 2021 | 1–90 | — | Outro: texto em bitmap (Type3 sem Unicode) | As 90 questões ficaram sem texto (`enunciado_vazio`/`alternativas_incompletas`). Já está registrado no TODO como pendência de OCR. |
| 2023 K | 76 | 23→24 | Figura em outra página (funciona) | A charge de Gorbachev fica no topo da página 24, e a questão começa na 23. A extração juntou as duas partes (`q76_completa1/2`) e pegou a figura. Fica como caso de referência que funciona. |
| 2023 K | 79–80 | 24–25 | Texto-base compartilhado com figura (funciona) | A tira do Armandinho foi para `textos_compartilhados["79-80"].figuras`. |

### Duas colunas

Cerca de 60 questões têm o recorte em 2 partes (coluna esquerda → coluna direita, ou página seguinte). As que foram conferidas estão corretas, como 2025 Q52, cuja 2ª parte traz as alternativas C–E na outra coluna. Duas colunas sozinhas, portanto, não são mais causa de recorte ruim. O problema aparece quando há uma faixa da largura da página no meio (2025 Q1).

### Texto-base compartilhado

Foram detectados 6 grupos em 2022, 4 em 2024, 5 em 2023, 6 em 2025 e 9 em 2026, todos com cabeçalho "TEXTO PARA AS QUESTÕES". Nenhum erro de associação foi encontrado. O caso 2023 Q28, porém, mostra que a questão do meio de um grupo pode perder o próprio enunciado.

### Falsos positivos dos sinais automáticos

- "cita imagem sem figura" acertou só 1 de 15 casos (2025 Q1). Nos outros, "imagem" e "mapa" aparecem em sentido figurado, ou a questão é de escala/gráfico sem figura.
- Nas páginas 6, 7, 14 e 17 de 2022, as "imagens não usadas" são uma única figura fatiada em faixas pelo PDF.

## Causas classificadas

| Causa | Casos | Tratamento |
|---|---|---|
| Duas colunas | 0 ruins (~60 questões em 2 partes, todas corretas) | — |
| Imagens laterais / faixa de página inteira | 2025 Q1 | Recorte manual da região (novo) |
| Texto-base compartilhado | 0 ruins (mas veja 2023 Q28) | — |
| Figura em outra página | 0 ruins (2023 Q76 funciona) | Recorte manual escolhendo a página (novo) |
| Outro: enunciado lido como figura | 2023 Q28, 2026 Q42 | Recorte manual com texto da região (2023 Q28) ou digitação/OCR (2026 Q42) |
| Outro: rodapé colado à alternativa | 2024 Q90 | Edição de texto na revisão; ajuste no extrator |
| Outro: PDF em bitmap | 2021 (90) | OCR (pendente) |

## O que foi feito

- **Recorte manual por região** (`features/questions/manual_crop.py`, `frontend/crop-editor.js`). Na revisão, o botão "Recortar região do PDF" (ou "Recortar do PDF" numa questão marcada como recorte ruim) abre a página do PDF. A pessoa escolhe a página, desenha o retângulo e o destino (enunciado ou alternativa A–E). O recorte é feito a 220 dpi, gravado em `img/crop_*.png` e entra no formulário como figura. O texto da camada de texto dentro da região pode preencher o enunciado e as alternativas. Ao salvar, tudo fica em `revisao.json` (`edits`), e a extração não muda. O PDF original vem de `simulados/uploads/` ou do `fuvest_acervo/`, desde que o sha256 seja igual ao gravado na extração. Sem PDF, a tela pede o envio (`POST /api/exams/{id}/source`), e só o arquivo idêntico é aceito.
- **Reprocessar uma questão** virou caso de uso (`features/questions/reprocess.py`), com a função de extração injetada e o mesmo contrato HTTP. A revisão humana (aprovação, matéria, conteúdo, observação) é mantida. Se a pasta da prova ainda não tem `revisao.json`, as decisões são herdadas da extração anterior via `preserve_reviews`.

## Pendências para o extrator

São `TODO(cross-agent)` para o domínio de `extractor.py`, que não foi alterado neste trabalho:

1. Sinalizar `enunciado_vazio` mesmo quando a questão tem figura (2023 Q28, 2026 Q42). Hoje essas questões passam sem nenhum aviso.
2. Imagens raster de coluna lateral na mesma faixa vertical do texto de uma questão deveriam virar figura dela (2025 Q1).
3. Recortar a faixa da largura da página que fica abaixo de duas colunas com a largura da página inteira (2025 Q1).
4. Remover o aviso de rascunho ("O que for escrito no rascunho não será considerado na correção") do texto das alternativas (2024 Q90).
5. Bloco com a camada de texto ilegível tratado como figura (2026 Q42): sinalizar para OCR.
