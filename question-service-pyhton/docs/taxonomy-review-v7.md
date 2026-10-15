# Revisão editorial da taxonomia — fuvest-2027.v7

Revisão feita por agentes em paralelo (um por matéria) em 2026-10-15: auditoria da proposta `docs/fuvest-taxonomy-proposal.json` contra o guia oficial, subconteúdos novos, mudanças de lugar e fontes com página. Nenhum ID foi removido; nós substituídos ficam com `deprecated: true` e `replaced_by`. As dúvidas de cada matéria abaixo são para a revisão humana.


## Arte — passe editorial (base fuvest-2027.v6)

### Contagens
| | antes | depois |
|---|---|---|
| Áreas | 5 | 5 |
| Conteúdos | 18 | 20 |
| Subconteúdos | 42 | 58 |

Nenhum id removido ou alterado. Nenhum nó deprecado (não há duplicatas verdadeiras).

### Auditoria contra o PDF
O Guia FUVEST 2026 (`docs/fuvest2026_guia-provas-programas.pdf`) lista para Arte **os mesmos seis Objetos de Conhecimento** nas Competências 1, 2, 3, 4 e 9 (p. 3, 8–9, 12, 15, 20): Elementos da linguagem, Materialidades, Mediação cultural, Patrimônio cultural, Processo de criação e Saberes estéticos e culturais. Todos já tinham conteúdo próprio na árvore. As habilidades acrescentam: relação da arte com as dimensões social/política (p. 2, 8, 12, 14), matrizes indígenas/africanas/europeias (p. 3), legitimação das manifestações artísticas (p. 8, 15) e tecnologias digitais/TDIC/remediação (p. 3, 12, 19). O guia 2027 (p. 14) só confirma Arte como disciplina da 1ª fase.

### Integrado da proposta (Antigravity)
- A proposta mapeia 1:1 os conteúdos atuais (`current_taxonomy_match`) e não traz conteúdo novo; a reorganização de áreas proposta não foi adotada (a atual é equivalente e mais coerente).
- Os aliases da proposta foram incorporados nos conteúdos (ex.: "linguagem cênica", "dramaturgia", "arte originária", "Semana de 22", "filosofia da arte", "curadoria"), sem colisões.
- Fontes precisas: todos os nós ganharam uma fonte do guia 2026 (`file`, seção "Objeto de conhecimento: …" ou "Habilidade: …", `printed_pages`, `competencias`) no nível mais específico. Mantida a fonte genérica do programa 2027.
- Descrições boilerplate ("Conteúdo de Arte: X.") foram reescritas a partir do texto oficial.

### Mudanças da revisão
**Movido**
- `arte.elementos-da-linguagem-artistica.elementos-visuais` → conteúdo Artes visuais (música, dança e teatro já tinham seus elementos dentro da própria linguagem).

**Renomeados**
- Área `arte.area.outros-conteudos`: "Outros conteúdos programáticos" → **"Arte e cultura digital"** (fonte: habilidades das Competências 1, 3 e 9). Não pode ficar vazia nem ser removida, e o tema digital tem base própria no programa.
- `arte.elementos-da-linguagem-artistica.leitura-de-imagens` → "Leitura e análise de obras" (alias "leitura de imagens").
- `arte.danca.dancas-populares-e-cenicas` → "Danças populares e tradicionais" (a dança cênica foi para o subconteúdo novo).
- `arte.arte-sociedade-e-politica.arte-engajada` → "Arte engajada e de protesto".
- Descrição corrigida: "Música erudita e experimental" só falava de nacionalismo; agora inclui dodecafonismo, música concreta e experimental.

**Conteúdos novos (lacunas de história da arte)**
- `arte.content.arte-europeia-da-antiguidade-ao-seculo-xix`: arte antiga e medieval; Renascimento; Barroco europeu; Neoclassicismo, romantismo e realismo.
- `arte.content.arte-academica-e-seculo-xix-no-brasil`: Missão Artística Francesa e Academia Imperial; Pintura histórica e acadêmica.

**Subconteúdos novos em conteúdos existentes**
- Dança: Balé, dança moderna e contemporânea · Teatro: Gêneros e estéticas teatrais
- Elementos da linguagem: Signos, símbolos e iconografia; Hibridismo entre linguagens (citado no próprio OK)
- Arte contemporânea: Pop art e cultura de massa · Modernismo: Arquitetura moderna brasileira
- Mediação: Agentes e práticas de mediação · Patrimônio: Arte pública e monumentos
- Saberes estéticos: Papel do artista na sociedade · Digital: Remediação, multimídia e transmídia

**Aliases**: nenhum nome ou alias repetido (casefold) em toda a matéria; nenhum nó lista o próprio nome como alias.

### Dúvidas para o revisor humano
1. Questões antigas rotuladas em "Danças populares e cênicas" sobre dança contemporânea agora caem em "Danças populares e tradicionais"; talvez valha relabelar para `arte.danca.bale-danca-moderna-e-contemporanea`.
2. O id `arte.area.outros-conteudos` passou a nomear "Arte e cultura digital". Se o nome do id incomodar, a alternativa é mover o conteúdo digital para "Criação e materialidades" e deprecar a área. Mas hoje o validador recusa área sem filhos.
3. Os dois conteúdos de história da arte (europeia pré-vanguardas e Brasil século XIX) não aparecem nominalmente no programa; entraram pelo OK "Patrimônio cultural" ("estudo da História da Arte") e pela habilidade de patrimônio "de diferentes tempos e lugares". Confirmar se a granularidade é desejada.
4. Há sobreposição com Literatura e História (Semana de 22, antropofagia, Missão Francesa, Brecht). Os aliases são únicos dentro de Arte, mas podem coincidir com os de outras matérias.
5. O comando de validação do brief tem erro de sintaxe (`yield` dentro de list comprehension). Validei com a função `ids` reescrita como gerador normal: o resultado foi `missing ids: set()`.


## Biologia: revisão editorial da taxonomia (base fuvest-2027.v6)

Fonte oficial conferida: `docs/fuvest2026_guia-provas-programas.pdf`, Ciências da Natureza, objetos de conhecimento (OK) de Biologia nas p. 29 (Habilidades 1), 31 (Habilidades 2) e 33 (Habilidades 3). Habilidades nas p. 28–30 e 31–32. A numeração impressa coincide com a página do PDF.

### Contagens

| | Áreas | Conteúdos | Subconteúdos |
|---|---|---|---|
| Antes | 7 | 54 | 203 |
| Depois | 7 | 57 | 213 |

Nenhum id foi removido ou alterado, e nenhum nó foi marcado como `deprecated`. Validação: `validate_content_taxonomy` passa, `missing ids: set()`, e não há colisão de nome ou alias (casefold) entre nós da matéria.

### Integrado da proposta (Antigravity)

- A proposta só traz aliases e `source_ref` por página: os 54 conteúdos coincidem com os da árvore atual, e nada novo veio dela. Os aliases sem colisão foram para os conteúdos (ex.: "neodarwinismo", "cladística", "herança mendeliana", "DNA recombinante", "vigilância sanitária"). Os aliases que já pertencem a um subconteúdo ficaram só no subconteúdo, para não haver ambiguidade (ex.: "daltonismo", "hemofilia", "briófitas", "osmose", "CRISPR").
- Fontes precisas: todo conteúdo ganhou uma fonte do Guia 2026 (`file`, `section` com o número da Habilidade, `printed_pages` e `object` com o texto literal do OK). Os subconteúdos herdam essa fonte. Os conteúdos que não são OK e vêm só de uma habilidade (Origem da vida, Conservação, Extinções, Juventudes, Relações ecológicas, Vírus) usam a fonte da habilidade (`skill`) com `basis_note` explicando.
- A proposta coloca "Importância dos microrganismos" em Ecologia e "Mitose e meiose" junto de genética e citologia. Os dois foram movidos (ver abaixo).

### Item do TODO: vírus, reinos e grupos vegetais

- **Vírus**: virou conteúdo próprio, `biologia.content.virus`, na área Evolução e diversidade. O subconteúdo antigo `biologia.filogenia-e-classificacao.virus` foi movido para lá e renomeado "Estrutura e características dos vírus". Novos subconteúdos: "Ciclos de replicação viral" e "Retrovírus e transcrição reversa".
- **Reinos**: novo conteúdo "Bactérias, protistas e fungos" (`biologia.content.bacterias-protistas-e-fungos`).
  - Novo subconteúdo: "Bactérias e arqueas".
  - Movido e renomeado: `…filogenia-e-classificacao.protistas-e-fungos` virou "Protistas: protozoários e algas".
  - Novo subconteúdo: "Fungos".
  - "Reinos e domínios" (o conceito de classificação) continua em Filogenia e classificação.
- **Grupos vegetais**: não virou conteúdo próprio. O OK oficial ("Adaptações morfológicas e reprodutivas das plantas ao ambiente terrestre") já é esse conteúdo, então `…grupos-vegetais` foi movido para "Adaptações das plantas ao ambiente terrestre" e renomeado "Grupos vegetais e conquista do ambiente terrestre".
- Resultado: "Filogenia e classificação" ficou só com Cladogramas, Sistemática e nomenclatura, e Reinos e domínios.

### Outras mudanças

**Movimentos**
- "Mitose e meiose" saiu de "Outros conteúdos programáticos" para Organização celular e metabolismo. É um OK da Habilidade 1.
- "Importância dos microrganismos" saiu de "Outros conteúdos programáticos" para Ecologia e ambiente. É um OK da Habilidade 2.

**Conteúdo novo para OK que faltava**
- "Mudanças ambientais e ocorrência de doenças" (OK p. 33), na área Saúde. Subconteúdos: "Desmatamento e doenças emergentes", "Clima e doenças transmitidas por vetores" e "Urbanização, poluição e doenças".

**Subconteúdos novos (lacunas frequentes na FUVEST)**
- "Proteínas e enzimas", em Células.
- "Quimiossíntese", em Fotossíntese.
- "Embriologia e anexos embrionários", em Adaptações dos grupos animais.

**Renomeações**
- Conteúdo "Herança ligada ao X" virou "Herança ligada aos cromossomos sexuais". Segue a redação do OK e resolve o nome igual ao do filho.
- "Homozigoto e heterozigoto" virou "Homozigose, heterozigose e dominância".

**Descrições**
- Todos os conteúdos trocaram "Conteúdo de Biologia: X." por uma descrição real.
- Sobreposições esclarecidas:
  - "Resistência a antibióticos e pesticidas" (o mecanismo evolutivo) e "Antibióticos e resistência bacteriana" (saúde pública).
  - Sistema reprodutor e gametogênese.
- Protozooses e verminoses agora citam os parasitas listados no OK. Giardíase e tricomoníase foram acrescentadas.

**Aliases**
- Removido "gametogênese" de "Sistema reprodutor masculino e feminino". Colidia com o subconteúdo Gametogênese.
- Removido "bactéria" de Célula procariótica, agora coberto por "Bactérias e arqueas".
- Removido "vírus" do antigo subconteúdo Vírus, que colidiria com o novo conteúdo.
- Removidos os aliases iguais ao próprio nome (ex.: Metamorfose, Fotoperiodismo, Alternância de gerações).

### Dúvidas para o revisor humano

1. **Área "Outros conteúdos programáticos"**: ficou só com "Origem da vida e atmosfera primitiva". O correto seria mover esse conteúdo para Evolução e diversidade, mas a área ficaria vazia e o validador recusa área sem filhos. Uma área não pode ser apagada por regra. Decidir se a área é renomeada ou se o validador passa a aceitar área `deprecated` vazia.
2. **Rótulos antigos que mudaram de sentido**: "Protistas e fungos" foi dividido. Rótulos antigos sobre fungos no id `…protistas-e-fungos` agora apontam para "Protistas: protozoários e algas", e precisam ser revisados. O risco é baixo, porque os subconteúdos são de hoje e ainda não têm gabarito.
3. **Mitose e meiose**: ficou em Organização celular. A proposta sugeria a área de Genética. As duas leituras são defensáveis.
4. **Conteúdos pequenos, possíveis fusões futuras**:
   - "Poliploidia" com "Segregação cromossômica e alterações cromossômicas".
   - "Movimentos de nastia" com "Hormônios vegetais e tropismos".
   - "Características dos insetos" com o subconteúdo Artrópodes.
   - Não fundi nenhum, para evitar deprecações.
5. **Lacunas não cobertas**: histologia animal (tecidos e contração muscular) não está no programa oficial e não foi acrescentada. Avaliar se vale um conteúdo editorial.
6. **Fluxo de deprecação**: `taxonomy.py` não trata `deprecated` (opções de etapa 1 e refinamento). Nesta matéria não houve deprecação, mas outras matérias podem precisar.


## Educação Física — revisão editorial (base fuvest-2027.v6)

Fonte oficial: `docs/fuvest2026_guia-provas-programas.pdf`, Linguagens e suas Tecnologias. Educação Física tem objetos de conhecimento nas Competências 1 (p. 2), 2 (p. 8), 3 (p. 11), 6 (p. 17), 7 e 8 (p. 18) e 9 (p. 19). As Competências 4 e 5 não têm OK de Educação Física. O guia 2027 só cita a matéria na lista da BNCC.

### Contagens

| | antes | depois |
|---|---|---|
| Áreas | 3 | 3 |
| Conteúdos | 18 | 18 |
| Subconteúdos | 39 | 51 (+12) |

Nenhum id foi removido ou alterado, e nenhum nó foi depreciado. A validação passa com `missing ids: set()` e não há nome ou alias repetido (casefold) dentro da matéria.

### O que entrou da proposta Antigravity

- Os 18 tópicos da proposta já batiam 1:1 com os conteúdos atuais (`current_taxonomy_match`). Por isso não houve mudança de estrutura.
- A proposta reorganiza as áreas ("Saúde, corpo e sociedade", "Cidadania, inclusão e políticas"). Não adotei esse agrupamento: ele mistura conteúdos sem ganho de diagnóstico, e as áreas atuais seguem bem os OKs.
- Aliases integrados nos conteúdos: jogos populares, brinquedo e cultura, danças folclóricas, dança e cultura, esporte e competição, esportes coletivos/individuais, ginástica rítmica/artística, artes marciais, corpo e cultura, gestualidade cultural, imagem corporal, mídia e corpo, exercício físico, aptidão física, prevenção de doenças, estilo de vida ativo, ergogênicos, suplementação esportiva, inclusão no esporte, acessibilidade esportiva, fair play, regulamento esportivo, organização competitiva, lazer e políticas públicas, esporte e estado, direito ao esporte, expressão corporal, gesto e cultura, tecnologia no esporte, esporte eletrônico, esporte e meio ambiente, esportes na natureza, profissões do esporte, educação física como profissão, tempo livre.
- Os aliases "wearables", "turismo de aventura" e "ginástica laboral" foram para os subconteúdos mais específicos.
- Aliases descartados porque duplicariam nomes ou aliases já existentes e tornariam a resolução ambígua:
  - "capoeira" e "judô" em Lutas
  - "sedentarismo" em Atividade física
  - "doping" em Suplementos
  - "paralimpíadas" em Preconceito
  - "padrão de beleza" no conteúdo (ficou no subconteúdo Padrões de beleza)
- Fontes: todos os conteúdos e subconteúdos trocaram a fonte genérica (pp. 2–20) por fontes precisas. Cada uma traz o arquivo, a Competência, a página e o texto literal do objeto de conhecimento. Nos subconteúdos editoriais, cada fonte mantém o `basis_note` editorial. Áreas e matéria ganharam a lista de páginas.

### Subconteúdos novos (OK oficial que faltava ou lacuna de diagnóstico)

- `educacao-fisica.esportes.esportes-de-marca-precisao-e-tecnico-combinatorios`: completa a classificação dos esportes pela lógica interna (BNCC).
- `educacao-fisica.ginasticas.ginasticas-de-conscientizacao-corporal`: yoga, pilates; separado de "competição".
- `educacao-fisica.ginasticas.ginastica-geral`
- `educacao-fisica.regras-e-organizacao-das-praticas-corporais.tecnicas-e-taticas`: OK C9, "técnicas e táticas".
- `educacao-fisica.historia-das-praticas-corporais.praticas-corporais-como-patrimonio-cultural`: OK C1 e C3, patrimônio imaterial.
- `educacao-fisica.linguagem-corporal-e-expressao-cultural.linguagem-corporal-e-convivencia-democratica`: OK C2.
- `educacao-fisica.praticas-corporais-como-textos-culturais.codigos-e-simbolos-das-praticas-corporais`: OK C9.
- `educacao-fisica.representacao-do-corpo-na-midia.redes-sociais-e-representacao-dos-corpos`: OK C7, impacto das mídias sociais.
- `educacao-fisica.atividade-fisica-saude-e-qualidade-de-vida.planejamento-de-rotinas-e-autocuidado`: OK C6, autonomia.
- `educacao-fisica.praticas-corporais-e-sustentabilidade.uso-sustentavel-dos-espacos-e-engajamento-comunitario`: OK C3 e C6.
- `educacao-fisica.preconceito-e-inclusao-nas-praticas-corporais.outros-preconceitos-nas-praticas-corporais`: gordofobia, LGBTfobia, etarismo (OK C2).
- `educacao-fisica.tecnologias-e-midias-nas-praticas-corporais.aplicativos-e-dispositivos-de-monitoramento`: OK C7.

### Renomeações e ajustes

- `esportes.esportes-de-invasao-e-rede` passa a se chamar "Esportes coletivos de invasão, rede/parede e campo e taco".
- `ginasticas.ginasticas-de-competicao-e-conscientizacao-corporal` passa a se chamar "Ginásticas de competição". A conscientização corporal virou um irmão novo.
- Sobreposições desfeitas por meio das descrições:
  - "Esporte de rendimento e mercado" (rendimento, megaeventos, doping) × "Mídia e espetáculo esportivo" (espetacularização pela mídia)
  - "Expressão cultural do movimento" × "Significados sociais do movimento" (identidades e relações de poder)
  - "Tecnologias no esporte" (rendimento, VAR) × "Aplicativos e dispositivos" (uso pessoal)
- As descrições genéricas dos conteúdos ("Conteúdo de Educação Física: X.") foram trocadas por descrições derivadas dos OKs. Todos os subconteúdos ganharam descrições mais precisas e aliases.

### Dúvidas para o revisor humano

1. O id `ginasticas-de-competicao-e-conscientizacao-corporal` agora significa só "competição". Rótulos antigos de questões sobre yoga ou pilates que apontem para ele devem ser reclassificados para o novo irmão.
2. "Redes sociais e representação dos corpos" ficou em Representação do corpo na mídia, mas o OK oficial está na C7 (tecnologias). Alternativa: movê-lo para "Tecnologias e mídias".
3. "Mobilidade ativa" ficou em Sustentabilidade, mas o OK de mobilidade é o de benefícios (C8). Mantive a posição e citei as duas fontes.
4. Alias "imagem corporal": fica só no conteúdo. O subconteúdo Padrões de beleza usa "transtornos de imagem corporal".
5. O snippet de validação do brief tem `yield` dentro de list comprehension, o que é SyntaxError no Python 3. Rodei uma versão equivalente com gerador recursivo.


## Filosofia — revisão editorial da taxonomia

Fonte principal: `docs/fuvest2026_guia-provas-programas.pdf`, Componente Filosofia, p. 35–38 (blocos de Habilidades 1–6 e os 6 Objetos de Conhecimento: Origens e desenvolvimento histórico da filosofia; Epistemologia; Ética e Filosofia Política; Estética; Argumentação e expressão filosófica; Antropologia filosófica). O guia 2027 local não detalha os objetos de conhecimento.

### Contagens
| | antes | depois |
|---|---|---|
| Áreas | 5 | 8 |
| Conteúdos | 26 | 26 |
| Subconteúdos | 72 | 83 |

Nenhum id foi removido ou alterado. Nenhuma depreciação foi necessária.

### O que veio da proposta (Antigravity)
- A proposta não traz conteúdos novos: os 26 tópicos coincidem com os conteúdos atuais. Aproveitei o agrupamento por objeto de conhecimento (áreas) e parte dos aliases (ex.: pirronismo, justificação epistêmica, etnociência, saberes tradicionais, consequencialismo, teoria do belo, condição humana, mercantilização da vida, doutrinas políticas, Estado de Direito).
- Aliases genéricos ou duvidosos da proposta foram descartados ("fake news filosófica", "consumismo filosófico", "etnocentrismo filosófico", "afetividade filosófica").
- Fontes: cada área e cada conteúdo ganhou uma segunda fonte com o guia 2026 (`file`, seção Habilidades/Objeto e página impressa). Os subconteúdos que o programa cita pelo nome (autores, teorias, temas) também receberam essa fonte específica. A fonte original do programa 2027 foi mantida.

### Mudanças estruturais
- Novas áreas, alinhadas aos objetos de conhecimento oficiais:
  - `filosofia.area.argumentacao-e-logica` "Argumentação e expressão filosófica", com Lógica e argumentação e Falácias;
  - `filosofia.area.estetica` "Estética";
  - `filosofia.area.antropologia-filosofica` "Antropologia filosófica", com cultura e trabalho, corpo/gênero/afetividade, finitude e sociedade de consumo.
- Conteúdos movidos (cada um segue o objeto oficial):
  - Fé e razão e Saberes de povos originários → Epistemologia (Habilidades 2);
  - Civilização e barbárie → História da filosofia (Habilidades 1);
  - Sociedade de consumo e do espetáculo → Antropologia filosófica (Habilidades 6).
- Subconteúdo movido: `hegel-e-o-idealismo-alemao` saiu de Renascimento e moderna e foi para Filosofia contemporânea, porque o programa lista Hegel na Filosofia Contemporânea.
- Áreas renomeadas (os ids continuam os mesmos):
  - `conhecimento-ciencia-e-logica` → "Epistemologia e filosofia da ciência";
  - `etica-pessoa-e-existencia` → "Ética";
  - `politica-e-sociedade` → "Filosofia política";
  - `outros-conteudos` → "Metafísica". As concepções metafísicas estão na Habilidade 1 do programa; a área foi mantida para não ficar vazia.

### Subconteúdos renomeados ou redefinidos
- `kant` → "Kant e o Esclarecimento". Agora é o Iluminismo e o texto "O que é Esclarecimento?". A epistemologia fica em Síntese kantiana e a ética em Deontologia kantiana, o que tira a sobreposição tripla.
- `origem-e-legitimidade-do-estado` → "Soberania, autoridade e legitimidade do Estado". Isso desfaz a duplicata com Contratualistas.
- `utilitarismo` → "Utilitarismo e consequencialismo".
- `liberdade` → "Liberdade política".
- `belo-e-gosto` → "Belo, feio e gosto".
- `filosofia-e-genero` → "Gênero e sexualidade".
- Fronteiras explicitadas nas descrições:
  - Foucault (contemporânea) × Poder disciplinar;
  - Filosofias não ocidentais (chinesa, indiana, árabe, africana) × Cosmologias indígenas;
  - Helenísticas × Ceticismo.
- Todos os conteúdos ganharam descrição real no lugar do texto genérico "Conteúdo de Filosofia: X".

### Subconteúdos novos (11), cada um com fonte no programa
- Origens: `filosofia-senso-comum-religiao-e-ciencia`
- Ceticismo: `crenca-opiniao-e-evidencia`
- Ciência: `ciencia-valores-e-neutralidade`
- Lógica: `linguagem-e-formas-de-expressao-filosofica`
- Teorias éticas: `dever-liberdade-e-autonomia`
- Democracia: `cidadania-e-direitos-civis`
- Liberalismo/socialismo: `estado-de-bem-estar-social-e-neoliberalismo`
- Estética: `o-que-e-arte-e-teorias-da-criacao`
- Corpo/gênero: `afetos-e-paixoes`
- Metafísica: `idealismo-realismo-e-materialismo` e `monismo-e-dualismo`

Os ids seguem o padrão `filosofia.<conteúdo>.<slug>`.

### Aliases
Nenhum nome ou alias se repete (casefold) entre nós da matéria, e nenhum nó repete o próprio nome como alias. Quando havia colisão, o termo ficou no nó mais específico e saiu do alias do conteúdo (ex.: Epicuro, Ockham, soberania, neoliberalismo).

### Dúvidas para o revisor humano
1. `fenomenologia-e-filosofia-da-linguagem` junta Husserl/Heidegger e Wittgenstein. Separar em dois subconteúdos seria melhor para o diagnóstico, mas mudaria o sentido do id. Mantive junto.
2. A área "Metafísica" usa o id `filosofia.area.outros-conteudos`, que ficou semanticamente estranho. Outra opção: mover o conteúdo para História da filosofia (Habilidade 1) e deixar a área vazia, mas o validador não aceita área vazia.
3. Civilização e barbárie foi para História pela posição no programa (Habilidade 1). O subconteúdo "Barbárie moderna" (Arendt/Adorno) tem forte interface com Filosofia política.
4. "Consumo e modernidade líquida" (Bauman) e "Sociedade do espetáculo" se sobrepõem a Sociologia. Avaliar a fronteira entre as matérias.
5. A URL exata do PDF do guia 2026 não foi confirmada. As fontes novas usam `url: https://www.fuvest.br` mais `file: docs/fuvest2026_guia-provas-programas.pdf`.
6. O comando de validação do brief tem `yield` dentro de list comprehension, o que é erro de sintaxe no Python 3.12+. Validei com uma versão equivalente usando laço `for`. Resultado: `missing ids: set()`.


## Física — revisão editorial (base fuvest-2027.v6)

Fonte conferida: `docs/fuvest2026_guia-provas-programas.pdf`, Objetos de conhecimento de Física nas p. 28 (Habilidades 1), 30 (Habilidades 2) e 32 (Habilidades 3). Gerado por `scratchpad/work/fisica_build.py`.

### Contagens
| | Antes | Depois |
|---|---|---|
| Áreas | 7 | 7 |
| Conteúdos | 53 | 53 |
| Subconteúdos | 177 | 187 |

Nenhum id foi removido ou alterado. Nenhum nó foi depreciado.

### O que veio da proposta (Antigravity)
- A estrutura da proposta coincide com os conteúdos atuais (todos têm `current_taxonomy_match`). Não havia conteúdo novo nela.
- Aproveitei os aliases dela nos conteúdos, menos os que estavam no lugar errado ou ambíguos: "atrito" em Leis de Newton, "lei de Coulomb" em Campo elétrico, "carga elétrica" em Corrente, "ciclo de Carnot" em Motores (foi para o novo subconteúdo Ciclo de Carnot), "peso" em Gravitação, "hidrelétrica"/"eólica" em Matriz.
- A proposta tinha a página só na matéria e na área. Agora cada conteúdo, área e subconteúdo diretamente citado traz uma fonte própria do guia 2026: `file`, `printed_pages`, `section` com o grupo de Habilidades e `object` com o texto literal do objeto de conhecimento. A fonte do programa 2027 continua como primeiro item.
- Aceitei a sugestão da proposta de pôr Motores a combustão em Termologia. Não aceitei mover Semicondutores, Energia solar e Matriz para Física moderna: ficaram em Eletricidade e em Energia, tecnologia e ambiente.

### Mudanças da revisão
**Movimentações (o id foi mantido)**
- `fisica.content.leis-da-termodinamica-e-maquinas-termicas`: de Mecânica para Termologia e termodinâmica (item do TODO, estava correto mover).
- `fisica.content.motores-a-combustao`: de Energia, tecnologia e ambiente para Termologia e termodinâmica (objeto "Processos Termodinâmicos em Motores a combustão").
- A área `fisica.area.outros-conteudos` deixou de ser um balaio e foi renomeada para **Oscilações e ondas**. Ela recebeu Ondas harmônicas, Ondas sonoras, Ressonância em tubos sonoros e Interferência, ao lado do MHS que já estava lá. Antigo nome mantido como alias.
- `fisica.area.ondas-e-optica` foi renomeada para **Óptica e radiação eletromagnética**, com "Ondas e óptica" como alias.
- `fisica.dinamica-de-foguetes.velocidade-de-escape-e-orbitas` foi para Gravitação e leis de Kepler, renomeado para "Velocidade de escape". Antes ele se sobrepunha a "Satélites e órbitas circulares".

**Renomeações**
- Conteúdo `campo-eletrico-e-forca-eletrostatica` → "Campo elétrico". A força entre cargas fica em Lei de Coulomb e o nome antigo virou alias.
- Conteúdo `interferencia-de-ondas` → "Interferência, difração e polarização". Nome antigo como alias.
- Subconteúdos:
  - "Grandezas vetoriais" → "Grandezas escalares e vetoriais"
  - "Imponderabilidade e peso aparente" → "Imponderabilidade em órbita" (peso aparente duplicava o subconteúdo de elevadores)
  - "Rendimento" → "Rendimento mecânico"
  - "Trabalho em transformações gasosas" → "Trabalho e ciclos no diagrama p×V"
  - "Transformação adiabática" (repetia o nome do pai) → "Compressão e expansão adiabáticas"
  - "Ciclo de motores a combustão" → "Ciclos Otto e Diesel"
  - "Riscos e usos da energia nuclear" → "Armas nucleares, usinas e riscos"
  - "Fontes renováveis e não renováveis" → "Comparação entre fontes de energia", segundo o objeto oficial
- Todas as descrições genéricas "Conteúdo de Física: X." foram reescritas. Também ajustei várias descrições de subconteúdos.

**Novos subconteúdos (10)**
- Leis da termodinâmica:
  - `fisica.leis-da-termodinamica-e-maquinas-termicas.ciclo-de-carnot`
  - `fisica.leis-da-termodinamica-e-maquinas-termicas.refrigeradores-e-bombas-de-calor`

  Os aliases "Carnot" e "refrigerador" saíram de "Máquinas térmicas e rendimento".
- `fisica.dinamica-de-foguetes.forcas-no-lancamento-e-no-voo`
- `fisica.movimento-harmonico-simples.energia-no-mhs`
- `fisica.ondas-sonoras-e-nivel-de-intensidade.notas-musicais-e-escalas` (objeto "notas musicais")
- `fisica.radioatividade-e-decaimento-radioativo.radiacoes-ionizantes-e-efeitos-biologicos` (objeto "radiações ionizantes")
- `fisica.evolucao-estelar.nucleossintese-e-origem-dos-elementos` (texto da Habilidade 2)
- Método científico:
  - `fisica.metodo-cientifico-e-fake-news.epistemologia-e-etica-na-ciencia` (objeto "Epistemologia e ética")
  - `fisica.metodo-cientifico-e-fake-news.unidades-e-analise-dimensional` (o alias "análise dimensional" saiu de Ordens de grandeza)
- `fisica.semicondutores-transistores-e-circuitos-integrados.condutores-isolantes-e-semicondutores` (objeto "Propriedade elétrica dos materiais")

**Aliases**
- Nenhum nome ou alias se repete dentro da matéria (comparação sem diferenciar maiúsculas, com casefold).
- Nenhum nó lista o próprio nome como alias.

### Dúvidas para o revisor humano
1. **Áreas de ondas e óptica.** A divisão entre Oscilações e ondas e Óptica e radiação eletromagnética reaproveita ids cujo slug não bate mais com o nome (`outros-conteudos`, `ondas-e-optica`). Os ids são opacos, mas confirme se a divisão é desejada. A alternativa era manter "Outros conteúdos" só com o MHS.
2. **Transformação adiabática.** Continua como conteúdo próprio e se sobrepõe a Leis da termodinâmica e a Gases. O ideal seria virar subconteúdo de Leis da termodinâmica. Não fiz porque o código ainda não trata `deprecated`/`replaced_by`, e um conteúdo esvaziado viraria folha da 1ª etapa.
3. **Fora dos objetos de conhecimento.** Hidrostática e Equilíbrio/torque não aparecem nominalmente na lista oficial. A fonte do guia traz `basis_note` explicando que foram enquadrados em "Dinâmica: Aplicação das Leis de Newton".
4. **Dinâmica de foguetes.** O conteúdo é fino (2 subconteúdos) e poderia ser absorvido por Impulso/Gravitação.
5. **Geração e transmissão de energia elétrica.** Ficou em Energia, tecnologia e ambiente. A proposta a colocava em Eletricidade, e o objeto "Geração de energia elétrica" está no grupo da Habilidade 1, junto com eletricidade.
6. **Efeito estufa.** Ficou em Energia/ambiente. A proposta o colocava em Termologia.


## Geografia: revisão editorial (base fuvest-2027.v6)

### Contagens
| | antes | depois (total) | depois (ativos) |
|---|---|---|---|
| Áreas | 7 | 8 | 8 |
| Conteúdos | 42 | 43 | 41 (2 descontinuados) |
| Subconteúdos | 122 | 142 | 140 (2 descontinuados) |

Nenhum id foi removido nem alterado. O validador passou e a checagem retornou `missing ids: set()`. Nenhum nome ou alias se repete após casefold, e nenhum nó usa o próprio nome como alias.

### O que veio da proposta (Antigravity)
- A proposta tem os mesmos 41 conteúdos do sistema (`current_taxonomy_match`) e não traz nenhum conteúdo novo. Dela aproveitei os aliases, distribuídos no nó mais específico possível. Aliases que eram na verdade subconteúdos, como "reforma agrária", "mineração", "OTAN", "ONU" e "offshore", desceram para o subconteúdo. Descartei aliases ruins, como "Norte Nordeste Sul Sudeste Centro-Oeste", e os que colidiam com outros nomes, como "desigualdades regionais".
- As páginas citadas pela proposta estão erradas. Ela fala em p. 37–46 e em "Habilidades 13–26" para economia. No guia 2026, Geografia fica nas **p. 38–47**, com as **Habilidades (1) a (32)**.

### Fontes
- Todo conteúdo, área e a matéria ganharam uma fonte `Guia de Provas e Programas FUVEST 2026` (`docs/fuvest2026_guia-provas-programas.pdf`), com o número da Habilidade, a página impressa e o texto literal do Objeto de Conhecimento. A fonte genérica do programa 2027 foi mantida.
- Cerca de 60 subconteúdos receberam a fonte da Habilidade que os nomeia diretamente. Exemplos: resíduos (H21), terras indígenas (H19), trabalho análogo ao escravo (H27), organismos ONU/FMI/OMC (H31) e Riscos (H14).

### Mudanças estruturais
- **Nova área** `geografia.area.questoes-socioambientais` ("Questões socioambientais e sustentabilidade"). Recebeu, de Natureza e ambiente, os conteúdos Impactos ambientais, Mudanças climáticas, Riscos e desastres e Legislação ambiental. De Espaço urbano e rural, recebeu Consumo e sustentabilidade, Gestão de resíduos e Turismo/patrimônio natural, que estavam mal colocados.
- `geografia.area.natureza-e-ambiente` foi renomeada para **"Dinâmicas da natureza"** (geografia física) e recebeu Relevo e estrutura geológica, que estava em "Outros".
- `geografia.area.outros-conteudos` foi renomeada para **"Desigualdades, cidadania e direitos humanos"**, ligada às H15, H16, H17 e H20. Contém Desigualdades regionais e acesso à internet e Segregação socioespacial, que veio de Espaço urbano e rural.
- Estrutura fundiária e conflitos no campo saiu de Economia e foi para Espaço urbano e rural. O motivo é a H5: "Espaço urbano e rural: conflitos pela terra".
- **Novo conteúdo** `geografia.content.categorias-e-pensamento-geografico`, em Cartografia e raciocínio geográfico. Cobre as H1, H2, H5 e H10, que não tinham nó próprio. Subconteúdos: Paisagem e transformações antrópicas; Espaço geográfico, lugar e região; Sociedades tradicionais e urbano-industriais; Correntes do pensamento geográfico.

### Descontinuados (`deprecated` + `replaced_by`)
- `geografia.content.chuvas-no-saara` → `geografia.content.climatologia-e-massas-de-ar`. Era um tema pontual, criado a partir de uma única questão.
  - `geografia.chuvas-no-saara.clima-desertico-e-variacoes` → `geografia.climatologia-e-massas-de-ar.tipos-de-clima`. Duplicava esse nó, cuja descrição agora cita os climas áridos.
  - `…saara-verde-e-mudancas-climaticas-passadas` foi **movido** para Mudanças climáticas e renomeado "Variações climáticas naturais e paleoclima".
- `geografia.content.energia-solar-no-brasil` → `geografia.content.matriz-energetica-brasileira`.
  - `geografia.energia-solar-no-brasil.expansao-da-energia-solar` → `geografia.energia-solar-no-brasil.potencial-solar-brasileiro`.
  - `…potencial-solar-brasileiro` foi **movido** para Matriz energética e renomeado "Energia solar".

### Renomeações
- "Matriz energética brasileira" passou a ser "Matriz energética e fontes de energia". O nome antigo ficou como alias.
- "Escala geográfica e raciocínio espacial" passou a ser "Escalas de análise e princípios do raciocínio geográfico" (H12).
- "Ordem mundial multipolar" passou a ser "Ordem mundial: bipolar, unipolar e multipolar".
- O subconteúdo homônimo "Meio técnico-científico-informacional" passou a ser "Conceito de meio técnico-científico-informacional", porque colidia com o nome do conteúdo pai.
- As descrições genéricas dos conteúdos ("Conteúdo de Geografia: X.") foram reescritas a partir dos Objetos de Conhecimento.

### Subconteúdos novos (20)
- Cartografia da violência e dos fluxos (H17, H28)
- Radiação solar e estações do ano
- Desmatamento e queimadas (H22)
- Órgãos e licenciamento ambiental (H23)
- Risco, vulnerabilidade e insegurança ambiental (H14)
- Transição energética e matriz mundial
- Fronteiras culturais e exclusão sociocultural (H8)
- Desindustrialização e reprimarização
- Industrialização e riscos ambientais (H24)
- Redes de transporte e logística
- Tecnopolos e geopolítica da ciência (H18)
- Trabalho análogo ao escravo (H27)
- Direito à cidade e políticas públicas (H16)
- Redes sociais e desinformação (H17)
- Migrações na América Latina (H29)
- Teorias e concepções geopolíticas (H10)
- Os 4 subconteúdos do novo conteúdo Categorias e pensamento geográfico

### Ajustes de aliases
- "ilha de calor" saiu de Fenômenos climáticos, porque duplicava Inversão térmica e ilhas de calor.
- "laterização" passou de Erosão para Formação dos solos.
- Os aliases iguais ao próprio nome foram removidos.

### Dúvidas para o revisor humano
1. O `taxonomy.py` ainda não filtra `deprecated`. Os conteúdos descontinuados continuam aparecendo como opção da 1ª etapa. É preciso filtrá-los nas opções e resolver `replaced_by` nos rótulos antigos.
2. O id `geografia.area.outros-conteudos` agora tem o nome "Desigualdades, cidadania e direitos humanos". A área não podia ficar vazia nem ser removida, então o id e o nome ficaram desalinhados. Aceitável?
3. "Fontes eólica e nuclear" continua como um único nó. Vale dividir em dois? Isso mudaria o significado de rótulos antigos.
4. "Amazônia e Mata Atlântica" e "Cerrado, Caatinga, Pantanal e Pampa" são agrupamentos arbitrários. Talvez valha um nó por bioma.
5. Os temas de energia, transportes, desindustrialização e radiação solar não aparecem nominalmente no programa. Entraram como detalhe editorial, por serem recorrentes na FUVEST.
6. As páginas da fonte genérica do programa 2027 ("34–54") não foram verificadas, porque o PDF do programa 2027 não está no repositório.
7. O snippet de validação do brief usa `yield` dentro de list comprehension, o que é um SyntaxError no Python 3.8+. Rodei uma versão equivalente.


## História — passe editorial (base fuvest-2027.v6)

### Contagens

| | Antes | Depois |
|---|---|---|
| Áreas | 7 | 7 |
| Conteúdos | 55 | 56 (+1) |
| Subconteúdos | 177 | 191 (+14) |
| Depreciados | 0 | 0 |

Nenhum id foi removido ou alterado (`missing ids: set()`). Nenhum nó foi depreciado: não encontrei duplicatas reais, só sobreposições, que resolvi reescrevendo descrições e aliases.

### Fonte oficial conferida

O componente História está em `docs/fuvest2026_guia-provas-programas.pdf`, nas **pp. 47–55** (Habilidades 1–30). A página impressa coincide com a página do PDF. A proposta cita pp. 46–54, o que está deslocado em uma página.

Cada área, conteúdo e subconteúdo agora tem duas fontes:
1. a fonte genérica do programa 2027, mantida;
2. uma fonte do guia 2026 com `file`, `section` (Habilidade n), `printed_pages` (página exata) e `object` (texto do objeto de conhecimento).

O campo `basis_note` diz se o objeto é oficial e literal ou se é derivação editorial.

### Integrado da proposta (Antigravity)

- **Aliases dos conteúdos:** foram adicionados. Ficaram de fora:
  - aliases que repetem nome ou alias de um subconteúdo, que já é o nó mais específico;
  - anos soltos ambíguos ("1789", "1917", "1948");
  - aliases ruins ("Africa scramble", "industrialização São Paulo", "WIC" etc.).
- **Estrutura de áreas:** a proposta tem 6 áreas cronológicas mais "Temas transversais". Adotei essa organização reaproveitando os 7 ids de área existentes.
- **Lacunas:** a proposta não cobre conteúdos novos (todos os tópicos dela já existiam). As lacunas abaixo vieram da leitura do PDF.

### Reorganização das áreas (ids mantidos, nomes novos)

- `sociedades-antigas-e-medievais` → **Antiguidade e Idade Média**
- `expansoes-colonizacao-e-sociedades-americanas` → **Expansão europeia, colonização e independências nas Américas**
- `revolucoes-e-transformacoes-economicas` → **Europa moderna, revoluções e capitalismo (séculos XV–XIX)**
- `brasil-imperio-e-formacao-republicana` → **Brasil: Império e Primeira República (1808–1930)**
- `brasil-contemporaneo-cidadania-e-memoria` → **Brasil republicano: de 1930 aos dias atuais**
- `conflitos-regimes-e-relacoes-globais` → **Século XX e mundo contemporâneo**
- `outros-conteudos` → **Temas transversais: memória, cidadania, trabalho e ambiente**. O id ficou semanticamente velho, mas não pode mudar.

### Conteúdos movidos de área (19)

- **Para Expansão/Américas:** Independências na América espanhola (vinha de "Outros").
- **Para Europa moderna:** Renascimento e Reforma, Absolutismo e mercantilismo (vinham de "Outros"); Imperialismo e neocolonialismo (vinha de Conflitos).
- **Para Brasil 1930+:**
  - Revolução de 1932 e Integralismo (vinha de Brasil Império);
  - República de 1945 a 1964 (vinha de Outros);
  - Ditadura militar no Brasil (vinha de Conflitos).
- **Para Século XX:**
  - Revoluções Mexicana e Russa, Neoliberalismo e globalização (vinham de Revoluções);
  - Contestação de 1968 (vinha de Brasil contemporâneo).
- **Para Temas transversais (8 conteúdos que estavam em Brasil contemporâneo):** Patrimônio/memória, Civilização e barbárie, Doutrinas políticas, DUDH, Direitos trabalhistas, Migrações, Resistência indígena e africana, Diáspora africana.

### Subconteúdo movido (1)

- `historia.revolucao-industrial-e-capitalismo.revolucoes-liberais-do-seculo-xix` foi para o novo conteúdo **Liberalismo, nacionalismo e unificações no século XIX**.

### Renomeações (o nome antigo virou alias)

- Conteúdo: "Independências na América espanhola" → **Independências nas Américas**
- Conteúdo: "Crises políticas de Vargas" → **Era Vargas**
- Subconteúdo: "Stalinismo e outros autoritarismos" → **Totalitarismo e autoritarismos ibéricos**. O stalinismo já estava em Revolução Russa → "Guerra civil, NEP e stalinismo".
- Subconteúdo: "Liberalismo, socialismo e anarquismo" → **Liberalismo, socialismo, anarquismo e outras doutrinas**, para cobrir a lista oficial da Habilidade 28.

### Adições (lacunas do programa oficial ou muito cobradas na FUVEST)

- **Conteúdo novo:** `historia.content.liberalismo-nacionalismo-e-unificacoes-no-seculo-xix`. Base: H10, "Impérios e Estados nacionais", e H28.
- **Subconteúdos novos (14):**
  - Monarquias feudais e representação política (H19, "Poder e representação no Medievo")
  - Reinos e impérios africanos: Gana, Mali, Songai e Congo
  - Congresso de Viena e Restauração
  - Unificações italiana e alemã
  - Independência dos Estados Unidos (H3, "lutas democráticas nas Américas")
  - Estados Unidos no século XIX: expansão e Guerra de Secessão
  - Revolução Chinesa
  - Terrorismo e 11 de Setembro (H16)
  - Imagens e propaganda política (H6)
  - Democracia direta, indireta e representativa (H19)
  - Movimentos feministas e direitos das mulheres (H15/H30)
  - Trabalho em diferentes sociedades e culturas (H26)
  - Comunidades tradicionais, trabalho e recursos naturais (H21)
  - Acordos e conferências ambientais internacionais (H22)

### Revisão de sobreposições (só descrições e aliases)

- **Estado Novo × Trabalhismo:** "CLT" ficou só em Trabalhismo. Estado Novo tem a descrição centrada no golpe de 1937, no DIP e na censura.
- **Cultura e resistência (ditadura) × 1968 no Brasil:** separei produção cultural e censura (1964–85) do movimento estudantil de 1968.
- **Nova República × Brasil nos anos 1990:** a primeira é a vida político-institucional; a segunda é a política econômica neoliberal.
- **Globalização × Fim da Guerra Fria:** globalização ficou com a ordem pós-1991 e os blocos.
- **Movimento abolicionista × Quilombos × Pós-abolição:** abolicionista ficou restrito aos atores e campanhas dos anos 1880.
- **Liberalismo/socialismo (doutrinas) × Movimento operário (Revolução Industrial):** um trata dos conceitos, o outro do movimento social no século XIX.
- **Era napoleônica:** o Congresso de Viena passou para o subconteúdo novo.
- **Outras descrições reescritas:** Revolução de 1930, Revolução Iraniana/Golfo, Sociedade escravista (agora com patriarcalismo, H27) e Discursos civilizatórios.
- **Descrições dos conteúdos:** todas eram "Conteúdo de História: X." e foram reescritas com o escopo real.

### Higiene de aliases (requisito do coordenador)

- Nenhum nome ou alias se repete, em casefold, dentro da matéria. Em conflito, ficou com o nó mais específico (subconteúdo, depois conteúdo, depois área).
- Removidos os aliases iguais ao próprio nome: União Ibérica, Primavera Árabe.
- Removidas 61 duplicatas, quase todas aliases da proposta que repetiam nomes ou aliases de subconteúdos.

### Dúvidas para o revisor humano

1. **"Reinos e impérios africanos (Gana, Mali, Songai, Congo)"** ficou em "Civilizações antigas do Oriente e da África", na área Antiguidade, embora seja medieval. Alternativa: criar um conteúdo "África medieval".
2. **Revolução Haitiana** continua em Diáspora africana (Temas transversais). Poderia ir para Independências nas Américas.
3. **Independências nas Américas:** o id `independencias-na-america-espanhola` agora também cobre os EUA. Rótulos antigos continuam válidos.
4. **Revolução de 1932 e Integralismo** é um conteúdo separado de Era Vargas, o que gera alguma sobreposição com "Revolução de 1930" e "Estado Novo". Avaliar fusão por depreciação no futuro.
5. **Aliases que mudaram de nó** (ex.: "CLT", "Cruzadas"): se algum rótulo legado foi gravado por alias, confira o mapeamento legado (`legacy_mapping`).
6. **Snippet de validação do brief:** a versão em uma linha falha (`SyntaxError`, por causa de `yield` dentro de list comprehension). Validei com a função equivalente escrita em várias linhas.


## Inglês — revisão editorial (base fuvest-2027.v6)

### Contagens

| | antes | depois |
|---|---|---|
| Áreas | 4 | 4 |
| Conteúdos | 19 | 20 (+1) |
| Subconteúdos | 40 | 60 (+20) |
| Depreciados | 0 | 0 |

Nenhum id foi removido nem alterado. Validação com `validate_content_taxonomy`: ok, `missing ids: set()`. Nenhum nome ou alias se repete (casefold) dentro da matéria, e nenhum nó lista o próprio nome como alias.

### Fontes

O guia 2026 (`docs/fuvest2026_guia-provas-programas.pdf`) lista os Objetos de Conhecimento de Língua Inglesa nestas páginas: 1ª Competência (p. 3–4), 2ª (p. 9–10), 3ª (p. 12–13) e 5ª (p. 16–17). Todos os conteúdos e quase todos os subconteúdos receberam, além da fonte do programa 2027, uma fonte do guia 2026 com `section` (competência e habilidade), `printed_pages` e `object` com o texto oficial. Nos subconteúdos que aparecem nominalmente no programa, como metonímia, ambiguidade e preconceito linguístico, o `basis_note` passou a dizer "Objeto de conhecimento nomeado no programa oficial".

### O que foi integrado da proposta (Antigravity)

A proposta só traz os mesmos 19 conteúdos da árvore atual (`current_taxonomy_match`) e não acrescenta nenhum conteúdo novo. Dela foram aproveitados os aliases dos conteúdos: reading comprehension, multimodal text, connectives, deducing meaning, verbos frasais, World Englishes, imperialismo linguístico, misinformation etc. Quando um alias colidia com um filho, ficou só no nó mais específico. As fontes "guia2026 p. 7–21" da proposta foram trocadas por páginas e habilidades exatas.

### O que faltava em relação ao PDF (adicionado)

- **Conteúdo novo:** `ingles.content.discurso-ideologia-e-posicionamento-critico` (área Língua e contextos socioculturais). Cobre os OKs "Análise e compreensão dos discursos…" (C5, p. 17) e "Apreciação / Réplica" (C2, p. 9). Subconteúdos: `ideologias-e-visoes-de-mundo` e `apreciacao-e-replica`.
- **Figuras de linguagem** (C2 H5, p. 10): `ironia-em-ingles`, `eufemismo-e-hiperbole`, `metonimia-em-ingles`.
- **Opinião, fato e fake news** (C3 H2, p. 13): `exemplificacao-autoridade-e-generalizacao` (citação específica × generalização).
- **Sinonímia, antonímia e polissemia:** `ambiguidade-em-ingles` (OK "Ambiguidade", C3 H2, p. 13).
- **Variação linguística** (C5, p. 16–17): `preconceito-linguistico` e `registro-e-adequacao-linguistica`.
- **Divulgação científica** (C1 H3, p. 4): `organizacao-e-hierarquizacao-das-informacoes`.
- **Gêneros verbo-visuais** (C3 H1, p. 13): `postagens-em-redes-sociais-e-memes`.
- **Relações lógico-discursivas** (C1 H3, p. 4): `estrutura-argumentativa-do-texto` (tese/argumentos, problema/solução) e `conectivos-de-condicao-finalidade-e-exemplificacao`.
- **Tempos verbais** (C3 H3, p. 13): `marcadores-e-expressoes-adverbiais-de-tempo`, `present-perfect-e-past-perfect` e `oracoes-condicionais`.
- **Modais** (C3 H4, p. 13): `adverbios-e-expressoes-modalizadoras` e `modais-no-passado`.
- **Comparativos** (C2 H3 / C3 H5): `comparativos-de-igualdade-e-inferioridade`.
- **Pronomes** (C3 H6, p. 13): `pronomes-pessoais-possessivos-e-demonstrativos`.

### Revisão editorial

**Movimento**

- O conteúdo "Figuras de linguagem em inglês" saiu da área Língua e contextos socioculturais e foi para Vocabulário e construção de sentido. O id foi mantido.

**Renomeações** (ids mantidos)

- `ideia-principal-e-detalhes` → "Ideia principal e tema do texto". O alias *scanning* foi para `localizacao-de-informacoes`, porque antes os dois irmãos se sobrepunham.
- `proposito-e-publico-do-texto` → "Gênero, propósito e público do texto".
- `cartuns-e-tirinhas-em-ingles` → "Quadrinhos, tirinhas e cartuns em inglês" (inclui graphic novels).
- `conectivos-de-adicao-e-contraste` → "…adição, contraste e concessão".
- `expressoes-idiomaticas.expressoes-idiomaticas` → "Idioms e sentido figurado". Antes tinha o mesmo nome do conteúdo pai.
- `prefixos-e-sufixos` → "Derivação: prefixos e sufixos".
- `phrasal-verbs-comuns` → "Phrasal verbs frequentes". Agora separa sentido conhecido de inferência pelo contexto (`phrasal-verbs-em-contexto`).
- `substantivos-compostos-e-plural` → "Grupos nominais e plural". Antes sobrepunha `composicao-de-palavras`; agora trata do núcleo de noun phrases e do plural.
- `comparativos` → "Comparativos de superioridade".
- `pronomes-e-referencia` → "Referência anafórica e catafórica".
- `presentes-e-passados` → "Presentes e passados simples e contínuos". O present perfect foi para um nó próprio.
- `futuro-e-condicionais` → "Futuro". As condicionais foram para `oracoes-condicionais`.
- `modais-de-possibilidade-e-obrigacao` → "Modais de obrigação, conselho, permissão e capacidade".
- `modalizacao-e-grau-de-certeza` → "Modais de possibilidade e grau de certeza". Antes "possibilidade" aparecia nos dois irmãos.
- `metaforas-e-ironia-em-ingles` → "Metáfora e comparação em inglês". A ironia ganhou nó próprio.

**Descrições**

- Todos os conteúdos tinham a descrição genérica "Conteúdo de Inglês: X." e agora têm descrições reais. As descrições das áreas e da matéria também foram reescritas.

**Depreciações**

- Nenhuma. Não há duplicatas verdadeiras.

### Dúvidas para o revisor humano

1. Alguns subconteúdos foram divididos ou mudaram de foco: `futuro-e-condicionais` (agora "Futuro"), `presentes-e-passados` (sem o perfect), `metaforas-e-ironia-em-ingles` (sem a ironia) e `modais-de-possibilidade-e-obrigacao`. Segundo o TODO, os rótulos de subconteúdo ainda não têm gabarito, então o risco é baixo. Mesmo assim, se já houver rótulos antigos apontando para esses ids, é preciso conferir se tratavam de condicionais, perfect ou ironia.
2. `ambiguidade-em-ingles` ficou em Sinonímia/Polissemia, porque o fenômeno é lexical e estrutural. No programa oficial, porém, a ambiguidade aparece entre os gestos argumentativos (C3 H2). A alternativa seria colocá-la em "Opinião, fato e fake news".
3. Alguns subconteúdos editoriais não aparecem nominalmente no programa: `modais-no-passado`, `oracoes-condicionais`, `present-perfect-e-past-perfect`, `comparativos-de-igualdade-e-inferioridade` e `conectivos-de-condicao-finalidade-e-exemplificacao`. Entraram pela frequência em provas e pela utilidade no diagnóstico. Confirmar se valem como nós próprios.
4. O novo conteúdo "Discurso, ideologia e posicionamento crítico" se aproxima de `implicitos-e-intencao-do-autor` e de "Opinião, fato e fake news". Avaliar se a fronteira está clara para o classificador.
5. A "Voz passiva" continua dentro de "Tempos verbais e marcadores de tempo". O programa não a nomeia; poderia ir para um conteúdo próprio de estruturas verbais.
6. O snippet de validação do brief tem um erro de sintaxe (`yield` dentro de list comprehension). A validação foi rodada com uma versão equivalente em `def` normal.


## Literatura: revisão editorial (base fuvest-2027.v6)

### Contagens
| | Antes | Depois |
|---|---|---|
| Áreas | 5 | 6 |
| Conteúdos | 26 | 39 |
| Subconteúdos | 63 | 91 |

Nenhum id foi removido nem alterado (`missing ids: set()`). Nenhum nó foi marcado como deprecated. Nenhum nome ou alias se repete (casefold) dentro da matéria, e nenhum nó tem o próprio nome como alias.

### Fontes
- Objetos de conhecimento: `docs/fuvest2026_guia-provas-programas.pdf`, Linguagens e suas Tecnologias. Gêneros literários estão na p. 7 (1ª Competência, Habilidade 9). Intertextualidade, lista de leitura, tradições indígenas, africanas e latino-americanas e períodos estão nas pp. 15–16 (4ª Competência, Língua Portuguesa, Habilidades 1 e 2). Essas fontes foram adicionadas no conteúdo ou subconteúdo mais específico, com `object` e `printed_pages`.
- A lista de leitura obrigatória não está nos PDFs locais. Usei a página oficial da FUVEST: https://www.fuvest.br/fuvest-renova-sua-lista-de-leituras-obrigatorias-para-o-vestibular-2026-2029/ (listas de 2026 a 2029).

### Integrado da proposta (Antigravity)
A proposta só repete os conteúdos atuais. Ela não traz objetos novos nem páginas do guia. Integrei os aliases úteis, como "trovadores", "cantigas medievais", "Padre Vieira", "cultismo e conceptismo", "neoclassicismo", "PALOP", "lista FUVEST", "obras do vestibular", "teoria dos gêneros", "paródia literária", "Terra sonâmbula", "Manuelzão e Miguilim" e nomes de autores. Descartei estes:
- "José Saramago relações" em literaturas africanas, porque está errado.
- "literatura afro-brasileira" em Água funda, porque é discutível.
- "Inocência" em Romantismo: prosa. Ele foi para o subconteúdo de romance urbano e regionalista.

### Mudanças da revisão
**Nova área** `literatura.area.leituras-obrigatorias-fuvest` ("Leituras obrigatórias FUVEST"). Ela tem um conteúdo por obra da lista vigente, cada um com 2 subconteúdos (enredo/personagens e forma/contexto):
- Lista de 2027 (9 obras): Opúsculo humanitário (Nísia Floresta), Nebulosas (Narcisa Amália), Memórias de Martha (Júlia Lopes de Almeida), Caminho de pedras (Rachel de Queiroz), A paixão segundo G.H. (Clarice Lispector), Geografia (Sophia de M. B. Andresen), Balada de amor ao vento (Paulina Chiziane), Canção para ninar menino grande (Conceição Evaristo) e A visão das plantas (Djaimilia Pereira de Almeida).
- Só na lista de 2026, mantidas porque o banco tem questões da FUVEST 2026: O Cristo cigano (Sophia) e As meninas (Lygia Fagundes Telles).

**Movido** `literatura.content.conteudo-de-leitura-obrigatoria`: saiu de "Gêneros, formas e leitura literária" e foi para a nova área. Foi renomeado para "Lista de leitura obrigatória: visão de conjunto". O subconteúdo `...lista-de-leituras-obrigatorias-fuvest` foi renomeado para "Características e estilo das obras da lista", que é o objeto da p. 16.

**Novos conteúdos em Períodos:**
- `literatura.content.quinhentismo`, com os subconteúdos literatura de informação e literatura jesuítica/Anchieta.
- `literatura.content.modernismo-geracao-de-45-e-prosa-instrumentalista`, com os subconteúdos João Cabral/poesia de 45 e prosa instrumentalista de Rosa e Clarice.
- Os períodos foram reordenados em ordem cronológica.

**Novos subconteúdos:**
- Gêneros: "Gêneros híbridos: ensaio, memórias e poema em prosa", útil para Opúsculo, Memórias de Martha e O Cristo cigano.
- Intertextualidade: "Diálogo das obras com o presente", que é o objeto da p. 16.

**Renomeado** `...genero-narrativo` para "Gênero narrativo: elementos da narrativa". Isso tira a sobreposição com "Conto, crônica e romance", que agora trata só das diferenças entre essas formas.

**Redistribuição de autores (mudança de sentido):**
- `literatura.literatura-contemporanea-brasileira.prosa-contemporanea` perdeu o alias "Clarice Lispector", que agora está no conteúdo de A paixão segundo G.H.
- `...poesia-contemporanea` perdeu o alias "João Cabral", que agora está no subconteúdo da geração de 45.
- As descrições dos dois foram atualizadas.

**Descrições:** as descrições genéricas ("Conteúdo de Literatura: X.") foram trocadas por descrições reais. Os conteúdos de "Autores e obras" (Quincas Borba, Campo geral, Água funda, Mia Couto) agora dizem que são obras de listas anteriores.

### Dúvidas para o revisor humano
1. A área `literatura.area.outros-conteudos` contém só "Poesia de Mário de Andrade". O lugar certo seria "Autores e obras", mas mover deixaria a área vazia, e o validador recusa área vazia. Não é possível manter a área com `deprecated` sem filhos. Sugiro mover o conteúdo e remover a área na integração, ou permitir área deprecated vazia.
2. Rótulos antigos em `prosa-contemporanea` e `poesia-contemporanea` sobre Clarice ou João Cabral devem ser remapeados para os novos nós.
3. Vale decidir se as obras novas da lista de 2028 (Conselhos à minha filha, João Miguel) entram desde já.
4. Os subconteúdos das obras foram escritos a partir de conhecimento editorial. Convém revisar enredo e nomes de personagens, sobretudo em O Cristo cigano e Geografia.
5. O comando de validação do brief tem `yield` dentro de list comprehension, o que é SyntaxError no Python 3.8+. Validei com uma versão equivalente usando um gerador.


## Língua Portuguesa — revisão editorial (base fuvest-2027.v6)

Fontes conferidas: `docs/fuvest2026_guia-provas-programas.pdf`, Língua Portuguesa nas Competências 1, 2, 3, 5 e 9 (pp. 4–7, 10–11, 14, 17, 20–21) e Redação (p. 21). Também `docs/fuvest2027-guia-provas.pdf`, critérios da redação (pp. 7, 10–12). A Competência 4 (pp. 15–16) trata de literatura e fica na matéria Literatura.

### Contagens

| | antes | depois |
|---|---|---|
| áreas | 5 | 5 |
| conteúdos | 36 | 38 |
| subconteúdos | 88 | 111 |

Nenhum id foi removido ou alterado. Nenhum nó foi marcado como `deprecated`.

### Integrado da proposta Antigravity

- A proposta não traz conteúdos novos: seus 36 tópicos correspondem um a um aos conteúdos atuais (`current_taxonomy_match`).
- Os aliases da proposta foram aproveitados nos conteúdos, ou no subconteúdo mais específico, quando não colidiam com o nome ou alias de outro nó. Exemplos: conectivos, progressão temática, desinformação, charge, HQ, redação, morfologia, período composto, voz passiva sintética, índice de indeterminação do sujeito, galicismo e dialeto.
- Ficaram de fora os aliases que repetem nomes de outros nós (ironia, metonímia, crase, paráfrase em dois lugares e outros) e os genéricos demais (dissertação em Gêneros; "figuras de linguagem" agora cobre três conteúdos).
- Fontes precisas: cada área, cada conteúdo e cerca de 30 subconteúdos ganharam uma fonte do guia 2026, com competência, habilidade, página e o texto do objeto de conhecimento. A fonte genérica do programa 2027 foi mantida em seguida.
- A estrutura de áreas da proposta foi aplicada em parte: Funções da linguagem foi para Leitura e Paráfrase/sinonímia foi para Léxico/semântica.

### Mudanças da revisão

**Movimentos (ids mantidos)**
- `content.funcoes-da-linguagem`: de Outros conteúdos para Leitura, interpretação e gêneros.
- `content.parafrase-e-sinonimia`: de Texto, discurso e argumentação para Léxico, semântica e recursos expressivos.
- `content.variacao-linguistica-e-preconceito-linguistico` e `content.norma-culta-e-mudanca-da-lingua`: de Gramática para a área `area.outros-conteudos`.

**Renomeações (o nome antigo ficou como alias)**
- Área `area.outros-conteudos` passou a se chamar "Variação linguística, norma e usos sociais da língua". Hoje ela reúne variação, norma culta e estrangeirismos (Competência 9).
- Área de semântica passou a se chamar "Léxico, semântica e recursos expressivos".
- "Paráfrase e sinonímia" passou a se chamar "Paráfrase, sinonímia e antonímia".
- "Derivação sufixal" passou a se chamar "Derivação e estrutura das palavras", porque o conteúdo já incluía prefixal e parassintética.
- "Flexão verbal e modo subjuntivo" passou a se chamar "Verbos: flexão, tempos e modos".
- Subconteúdos: "Paródia e paráfrase" passou a "Paródia, paráfrase e estilização"; "Modalizadores" passou a "Recursos gramaticais modalizadores"; "Interdiscursividade" passou a "Interdiscursividade e dialogia".

**Descrições:** os 36 textos genéricos dos conteúdos ("Conteúdo de Língua Portuguesa: X.") foram trocados por descrições reais. Também foram ajustadas descrições de subconteúdos que se sobrepunham, por exemplo artigo de opinião × tipos de argumento × réplica, e reportagem × discurso direto/indireto.

**Aliases:** o alias "crase" foi removido do nó Crase, porque repetia o próprio nome. "colocação pronominal" saiu de Pronomes e foi para o novo nó próprio. Verificado: nenhum nome ou alias se repete dentro da matéria (casefold) e nenhum nó usa o próprio nome como alias.

**Conteúdos novos (2)**
- `content.compreensao-global-e-inferencia` (Leitura). Subconteúdos: tema e ideia central; relação entre as partes do texto; pressupostos e subentendidos. É a lacuna de interpretação mais cobrada na FUVEST: até agora só havia conteúdos por gênero.
- `content.figuras-de-som-e-de-construcao` (Semântica). Subconteúdos: aliteração, assonância e onomatopeia; elipse, zeugma, pleonasmo e hipérbato; repetição, paralelismo e gradação. Base: "Figuras de Linguagem" e o atrelamento de elementos sonoros aos verbais (p. 5–6).

**Subconteúdos novos (17, além dos 6 dos conteúdos novos)**

| Conteúdo | Subconteúdo novo | Base no programa |
|---|---|---|
| Gêneros | contexto de produção, circulação e recepção | pp. 6, 11 |
| Operadores | problema/solução e definição/exemplos | p. 5 |
| Modalização | modalização epistêmica, deôntica e apreciativa | p. 6 |
| Persuasão | réplica e contra-argumentação | p. 11 |
| Produção textual | uso da coletânea e indícios de autoria | guia 2027 p. 11 |
| Retextualização | discurso direto, indireto e indireto livre | — |
| Paráfrase | antonímia | p. 6 |
| Divulgação científica | organização tópico-discursiva e fontes | p. 14 |
| Divulgação científica | resumo e resenha | p. 14 |
| Fake news | engajamento e temas controversos | p. 11 |
| Parcialidade | comparação de notícias sobre um mesmo fato | p. 11 |
| Classes gramaticais | colocação pronominal | — |
| Classes gramaticais | ordem dos constituintes da oração | p. 6 |
| Derivação | estrutura das palavras | — |
| Verbos | conjugação de verbos irregulares | — |
| Metáfora | catacrese, perífrase e antonomásia | — |
| Variação | níveis da variação (fonético, lexical, morfossintático, semântico) | p. 21 |

### Dúvidas para o revisor humano

1. O id `lingua-portuguesa.area.outros-conteudos` agora nomeia uma área com sentido próprio (variação/norma). O id ficou enganoso, mas é contrato. Confirmar se é melhor manter assim ou criar uma área nova e deixar esta como está.
2. O nó "Orações subordinadas substantivas e adjetivas" junta dois tipos. Dividir em dois mudaria o sentido do id. Ficou como está, só com aliases (restritiva/explicativa). Avaliar se vale criar um nó só para adjetivas e marcar este como deprecated.
3. Crase continua em Pontuação e ortografia. A proposta a associa a Regência. Mover manteria um id com o prefixo `pontuacao-e-ortografia`.
4. Formação de palavras e estrangeirismos não aparecem nominalmente nos objetos de conhecimento de Língua Portuguesa. Formação de palavras aparece em Inglês, e a fonte usada aqui foi "Morfossintaxe"/"morfologia". Para estrangeirismos, a fonte usada foi a variação lexical. Os dois são inferências editoriais.
5. "Ironia, metonímia e hipérbole" e "Metáfora e personificação" mantêm nomes por figura. Uma reorganização em figuras de palavra/pensamento seria mais didática, mas mexeria em muitos rótulos.
6. A Competência 1, Habilidade 9 (gêneros literários: crônica, romance, conto, poema) e a Competência 4 inteira ficaram com Literatura. Só a crônica tem conteúdo de interpretação aqui.
7. O snippet de validação do brief tem um `yield` dentro de list comprehension, que é erro de sintaxe no Python 3.8+. Validei com uma versão equivalente (gerador com `for`), e o resultado foi `missing ids: set()`.


## Matemática — revisão editorial (base fuvest-2027.v6)

Fonte conferida: `docs/fuvest2026_guia-provas-programas.pdf`, p. 22–26 (Habilidades 1–5 e Objetos de Conhecimento). A numeração impressa coincide com a do PDF.

### Contagens

| | Antes | Depois |
|---|---|---|
| Áreas | 6 | 6 |
| Conteúdos | 49 | 51 (1 descontinuado; 50 ativos) |
| Subconteúdos | 189 | 199 |

Nenhum id foi removido nem alterado. Foram criados 12 ids novos (2 conteúdos e 10 subconteúdos).

### O que veio da proposta (Antigravity)
- Os 48 conteúdos da proposta já existiam com o mesmo nome. Nenhum conteúdo novo veio dela.
- Aliases da proposta foram incorporados nos conteúdos, que antes não tinham nenhum. Ficaram de fora os ambíguos ou que colidiam com outros nós:
  - "complexos" em Conjuntos numéricos;
  - "escala" em Semelhança;
  - "cubo" em Poliedros;
  - "inequação exponencial/2º grau/modular" em Inequações logarítmicas;
  - "parábola";
  - "divisão de polinômios";
  - "Pitágoras";
  - "distribuição", "evento" e "relações trigonométricas".
- A divisão de áreas da proposta, com "Geometria analítica" e "Funções" como áreas próprias, **não** foi adotada, para não mudar a estrutura de áreas. Fica como dúvida.

### Fontes
- Toda área, conteúdo e subconteúdo passou a citar a página exata (22, 23, 24, 25 ou 26) e o bloco de Habilidades. O campo `object` traz o texto oficial do Objeto de Conhecimento.
- Áreas e conteúdos ganharam uma segunda fonte com `file: docs/fuvest2026_guia-provas-programas.pdf`.
- Nós fora do programa explícito usam um `basis_note` próprio, que diz que o nó não aparece nos Objetos de Conhecimento 2026/2027.

### Movimentações (id mantido)
- `content.amostragem-e-pesquisas`: de Números → Contagem, probabilidade e estatística (OK "População e amostra", p. 26).
- `content.pavimentacoes-do-plano`: de Outros conteúdos → Geometria e medidas (p. 25).
- `content.numeros-complexos` e `content.funcoes-trigonometricas-inversas`: → área `area.outros-conteudos`, renomeada para **"Tópicos complementares"** (alias "Outros conteúdos programáticos"). Os dois assuntos não estão nos OKs 2026/2027, que citam só reais e só seno, cosseno e tangente.
- `composicao-e-ponto-fixo-de-funcoes.conceito-de-funcao` → novo conteúdo "Conceito de função e análise gráfica".
- `inequacoes-logaritmicas.inequacoes-logaritmicas` e `.sistemas-com-logaritmos` → conteúdo "Logaritmos e funções logarítmicas".

### Descontinuados
- `matematica.content.inequacoes-logaritmicas`: `deprecated: true`, `replaced_by: matematica.content.logaritmos-e-funcoes-logaritmicas`. Ele se sobrepunha ao conteúdo Logaritmos na 1ª etapa. Foi renomeado para "Inequações logarítmicas (descontinuado)" e ficou sem filhos.

### Novos nós
- Conteúdo **Sequências numéricas**, com os subconteúdos "Lei de formação e padrões" e "Sequências definidas por recorrência" (OK "Sequências, progressões...", p. 23).
- Conteúdo **Conceito de função e análise gráfica**, com "Conceito de função, domínio e imagem" (movido), "Crescimento, decrescimento e zeros pelo gráfico" e "Transformações de gráficos" (OKs de gráfico de função, p. 24).
- Subconteúdos novos:
  - Erro em medições (OK "Noção de erro em medições");
  - Taxas e índices (OK "taxas e índices");
  - Posições relativas entre circunferências (p. 23);
  - Ângulos, retas paralelas e transversais;
  - Propriedades dos triângulos;
  - Tangência e posições relativas na circunferência (OKs de figuras geométricas e de posições relativas, p. 25).

### Renomeações e ajustes
- Colisões de nome e alias resolvidas, como pedido pelo coordenador:
  - "Mudança de base" virou "Mudança de base de numeração" e "Mudança de base de logaritmos";
  - o subconteúdo "Amplitude" virou "Amplitude total", e o alias de gráficos trigonométricos virou "amplitude de função periódica";
  - aliases iguais ao nome do próprio nó foram removidos.
  - Hoje não há nenhuma colisão casefold de nome ou alias na matéria.
- "Equações e inequações do 1º grau" (em Fatoração) virou "Equações do 1º grau e problemas". Ele duplicava "Inequações do 1º grau" de Funções afins.
- Outras renomeações:
  - "Coeficiente angular" → "Coeficiente angular, paralelismo e perpendicularidade";
  - "Posições relativas" → "Posições relativas entre ponto, reta e circunferência";
  - "Ângulos em polígonos" → "Ângulos internos, externos e diagonais de polígonos";
  - "União e evento complementar" → "União, interseção e evento complementar".
- Descrições específicas para todos os conteúdos, que antes eram genéricas ("Conteúdo de Matemática: X."), e para Arco duplo e Arco metade.
- Aliases novos ligados a termos do programa, entre eles: diagrama de árvore, polígono de frequência, ramos e folhas, eventos disjuntos, equiprováveis, base sexagesimal, capacidade, estimativa, apótema e tronco de pirâmide.

### Dúvidas para o revisor humano
1. O conteúdo descontinuado ficou sem filhos e por isso vira folha. O loader atual o listaria como opção da 1ª etapa. É preciso filtrar `deprecated` em `is_stage_one_option` e nas listagens.
2. Mover Números complexos e Funções trigonométricas inversas para "Tópicos complementares" é uma escolha editorial. A proposta os mantém em Números e em Trigonometria.
3. Outros subconteúdos fora do programa explícito foram mantidos no lugar, com `basis_note` de fora do programa: cônicas, matrizes, determinantes, secante/cossecante/cotangente, médias geométrica e harmônica, distribuição binomial, binômio de Newton, equações polinomiais e Girard. Falta confirmar se ficam.
4. Avaliar se a matéria deve adotar áreas próprias para Geometria analítica e Funções, como na proposta.


## Química — passada editorial (base fuvest-2027.v6)

### Contagens
| | antes | depois |
|---|---|---|
| Áreas | 7 | 7 |
| Conteúdos | 45 | 45 |
| Subconteúdos | 164 | 178 (+14 novos, 1 movido) |

Nenhum id foi removido nem alterado. Nenhum nó foi deprecado.

### Integração da proposta (Antigravity)
- A proposta só mapeia 1:1 os 45 conteúdos atuais (`current_taxonomy_match`) e não traz conteúdo novo. O que se aproveitou foram os sinônimos, filtrados para não colidirem com nomes ou aliases de outros nós. Ficaram de fora, por exemplo, "eletrólise" em Pilhas, "isótopos" em Radioatividade, "DBO" repetido, "tonoscopia" e "ebuliscopia" (grafia errada).
- Fontes: todo conteúdo agora traz a página impressa exata do guia 2026 (`docs/fuvest2026_guia-provas-programas.pdf`; a página impressa é igual à do PDF), a Habilidade (1: p. 28–29; 2: p. 30; 3: p. 32–33) e `official_objects` com o texto literal do Objeto de Conhecimento. Os subconteúdos herdam essas informações do conteúdo pai. Os subconteúdos novos que vêm do programa citam o próprio objeto.

### Lacunas do programa oficial que foram cobertas (subconteúdos novos)
- Ciclo da água (p. 29) e Interferência humana nos ciclos (p. 30): em Ciclos…, que foi renomeado para "Ciclos do carbono, da água, do nitrogênio e do enxofre"
- pH e impactos ambientais (p. 30): em Chuva ácida e poluição atmosférica
- Formação da atmosfera primária e secundária (p. 30): em Origem…, que foi renomeado para "Origem dos elementos químicos e da atmosfera"
- Elementos e substâncias essenciais à vida (p. 30): em Biomoléculas
- Modelo de partículas (p. 32): em Estados físicos e separação de misturas
- Obtenção de H₂, O₂ e N₂; Cloro, soda cáustica e ácido clorídrico; Ciclos de matéria e energia na indústria química (p. 33): em Produção industrial de compostos inorgânicos
- Reatividade, toxicidade e exposição (p. 29): em Resíduos e impactos da indústria química

### Revisão editorial
- Subconteúdos novos que costumam cair na FUVEST (sem citação nominal no programa): Transformações gasosas, Lei de Dalton das proporções múltiplas, Substâncias simples, compostas e alotropia, Caráter ácido e básico dos compostos orgânicos.
- Movido: `quimica.equilibrio-de-solubilidade.curvas-de-solubilidade` saiu de "Equilíbrio de solubilidade" e foi para "Soluções e concentração", com o novo nome "Coeficiente e curvas de solubilidade". O conteúdo é de soluções, não de Kps.
- Renomeado: `…carga-formal-e-numero-de-oxidacao` agora se chama "Carga formal", porque o Nox já está em Oxirredução. O nome antigo virou alias.
- Renomeado: `quimica.eletrolise.aplicacoes-industriais` agora se chama "Aplicações industriais da eletrólise". A descrição foi separada da do novo nó "cloro-soda": a eletrólise trata do processo, a produção industrial trata do produto e das aplicações.
- Descrições ajustadas para tirar sobreposições: "Propriedades físicas dos compostos orgânicos" não cobre mais acidez e basicidade (agora há nó próprio), "Ciclo do carbono" não cobre mais o efeito estufa antrópico, e também foram revistos "Esterificação e hidrólise" e "Equilíbrios em sistemas biológicos e industriais".
- As 45 descrições de conteúdo, que eram genéricas ("Conteúdo de Química: X."), foram reescritas. As descrições das 7 áreas foram atualizadas. Todos os conteúdos ganharam aliases.
- Colisões de alias resolvidas: "Haber-Bosch" ficou só em Síntese da amônia (saiu de Equilíbrios em sistemas…). "Nox" ficou só em Número de oxidação (saiu de Carga formal). O alias "hibridização" do nó Hibridização foi removido por ser igual ao nome. Não há nenhuma colisão de nome ou alias (casefold) na matéria.

### Dúvidas para o revisor humano
1. "Radiações e radioatividade na química" não tem Objeto de Conhecimento nominal em Química. A fonte aponta a Habilidade da p. 27 e o objeto de Física na p. 28. Vale avaliar se o tema deve continuar em Química.
2. "Origem dos elementos…" e "Radiações…" estão na área "Química, indústria e ambiente". Poderiam ir para "Matéria, estrutura e propriedades" (química nuclear). Mantive como estava para não mexer na classificação já feita.
3. "Substituição em cloretos de alquila" e "Fitorremediação" são conteúdos estreitos, fora do programa nominal e com 2 filhos cada. O primeiro se sobrepõe parcialmente a Reações orgânicas (substituição e eliminação). Avaliar se vale fundir via `deprecated` e `replaced_by`. Não fiz porque não é um duplicado verdadeiro.
4. O snippet de validação do brief tem `yield` dentro de list comprehension, o que é SyntaxError no Python 3.8+. Validei com uma versão equivalente, com laço explícito, e o resultado foi `missing ids: set()`.
5. O loader (`taxonomy.py`) não filtra `deprecated`. Isso não afeta esta matéria, mas afeta as outras.


## Sociologia — revisão editorial (base fuvest-2027.v6)

### Contagens

| | Antes | Depois |
|---|---|---|
| Áreas | 7 | 8 |
| Conteúdos | 35 | 45 |
| Subconteúdos | 81 | 141 |

São 71 nós novos (1 área, 10 conteúdos e 60 subconteúdos). Nenhum id foi removido e não houve deprecações.

### Auditoria da proposta (Antigravity)

- A proposta só cita as Habilidades 1–15 (p. 54–60) e apenas remapeia os 35 conteúdos já existentes. Não acrescenta nada novo. No guia 2026, o componente Sociologia vai da **p. 55 à p. 65, com 30 Habilidades**. As Habilidades 16–30 e parte das 3, 6, 7, 10, 12, 13 e 15 não tinham cobertura na árvore.
- Da proposta aproveitei os aliases úteis nos conteúdos, como Gemeinschaft e Gesellschaft, mandonismo local, americanização, durabilidade de produtos e moda rápida. Descartei os aliases artificiais, como "MST sociologia", "favela sociologia" e "coronelismo sociológico".
- Cada nó tem agora duas fontes: o programa 2027 (com o `object` oficial) e o guia 2026 (`docs/fuvest2026_guia-provas-programas.pdf`), com a Habilidade e a página exatas. Os subconteúdos que correspondem literalmente a um objeto de conhecimento trazem `basis_note` indicando isso. Os demais continuam marcados como editoriais.
- As descrições genéricas dos conteúdos ("Conteúdo de Sociologia: X.") foram reescritas com base nos objetos de conhecimento oficiais.

### Conteúdos novos (objetos do programa que faltavam)

- `genero-e-sexualidade` (H24)
- `religiao-laicidade-e-intolerancia` (H2, H24)
- `construcao-da-cidadania-no-brasil` (H27: herança colonial, cidadania inconclusa)
- `sociedade-civil-moral-e-justica-social` (H23: contratualismo, cooperação/competição, equidade)
- `minorias-pobreza-e-violencia` (H25)
- `emprego-desemprego-e-mercado-de-trabalho` (H19–20: CLT, PEA, tipos de desemprego, qualificação)
- `sentidos-e-desigualdades-do-trabalho` (H19, H22: sentidos do trabalho, dupla jornada)
- `riscos-e-questao-socioambiental` (H16–18: sociedade de risco, acordos, ambientalismo, sustentabilidade)
- `ciencia-tecnologia-e-dilemas-eticos` (H26: negacionismo, peritos, insensibilidade moral)
- `povos-indigenas-no-brasil` (H18, H27)

Também foram acrescentados subconteúdos nos conteúdos existentes:

- problema sociológico, revisão bibliográfica, crítica de fontes, análise de dados e perfil socioeconômico (H3, H30);
- vida caipira (Antonio Candido), modernização e imigração, subdesenvolvimento e dependência (H7);
- estrutura político-econômica da mídia e desigualdade digital (H6);
- poder e política, sistemas de governo e presidencialismo de coalizão (H28);
- movimentos sociais globais (H8);
- legislação da terra, terra e trabalho, conflitos no campo (H14);
- expansão urbana e ecologia humana, condomínios fechados e guetos, espoliação urbana, equipamentos culturais (H10, H12);
- sociedade de consumo e publicidade (H15), e inovação (H13);
- guerra e genocídio (H5);
- alienação, toyotismo, home office e empreendedorismo (H21);
- normas e controle social, educação e reprodução (H1, H23);
- gosto de classe (H4), direitos humanos e cidadania (H29), colonialismo (H2), grupos sociais (H1).

### Movimentações (ids mantidos)

- A área `outros-conteudos` deixou de ser uma área genérica e passou a se chamar **"Modernidade, ciência e ambiente"**. O id ficou o mesmo.
- Área nova `sociologia.area.campo-terra-e-povos-indigenas`. Recebe `questao-agraria-…` (vindo de outros-conteudos), `patriarcalismo-coronelismo-…` (vindo de estado-poder) e o novo conteúdo de povos indígenas.
- `divisao-do-trabalho-…` (de desigualdades) e `racionalizacao-e-burocracia` (de trabalho) foram para **fundamentos**, junto com a teoria clássica (H2).
- `evolucionismo-darwinismo-social-e-eugenia` saiu de fundamentos e foi para **Modernidade** (H5).
- `mobilidade-urbana` e `segregacao-socioespacial-e-periferias` saíram de desigualdades e foram para a área **cidade**.

### Renomeações principais

- O subconteúdo "Cultura popular, erudita e de massa" tinha o mesmo nome do conteúdo pai. Passou a "Hierarquias entre cultura popular, erudita e de massa".
- "Estado, formas de governo e cidadania" passou a "Poder, Estado e formas de governo". A cidadania ficou em conteúdos próprios.
- "Racismo estrutural e institucional" passou a "Tipos de racismo: injúria, racismo institucional e estrutural", seguindo o texto oficial da H24.
- "Desigualdade de renda e de gênero" passou a "Desigualdade de renda e interseccionalidade". Gênero ganhou conteúdo próprio.
- Outros nomes foram ajustados para o texto oficial: Fake news e comunicação política; Algoritmos, privacidade e bolhas digitais; Toyotismo, flexibilização e terceirização; Concentração fundiária; Moradia e problemas urbanos; e outros (lista completa no diff).

### Unicidade de nomes e aliases

Nenhum nome ou alias se repete na matéria (comparação em casefold), e nenhum nó tem o próprio nome como alias. Quando um alias se repetia, ele ficou no nó mais específico (o subconteúdo) e saiu do conteúdo. Por isso alguns conteúdos ficaram com poucos aliases ou nenhum.

### Dúvidas para o revisor humano

1. **Rótulos antigos que podem ter mudado de sentido:**
   - `desigualdade-de-renda-e-de-genero`: questões só de gênero agora iriam para `genero-e-sexualidade.preconceito-e-desigualdade-de-genero`.
   - `estado-formas-de-governo-e-cidadania.formas-de-estado-e-de-governo`: antes cobria presidencialismo e parlamentarismo, que agora estão em `sistemas-de-governo`.
   - `participacao-e-cidadania`.

   Vale reclassificar esses rótulos antigos.
2. Os ids `outros-conteudos` e `estado-formas-de-governo-e-cidadania` ficaram desalinhados dos novos nomes, porque um id não pode mudar.
3. Há sobreposição com Filosofia (contratualismo, Escola de Frankfurt), Geografia (questão agrária, urbanização, ambiente) e História (escravidão, indígenas). O conteúdo em comum é o mesmo, mas aqui ele está no recorte da Sociologia.
4. O programa 2027 detalhado não está no repositório. As páginas citadas são do guia 2026, que a FUVEST declara idêntico ao programa 2027.
5. O validador do brief tem um erro de sintaxe no Python 3.12 (`yield` dentro de list comprehension). Rodei a mesma checagem com uma função geradora e ela imprimiu `missing ids: set()`.

