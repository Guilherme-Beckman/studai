# Contrato — Relatórios de evolução (Fase 5)

Contrato fixado antes da implementação paralela. Backend (`features/reports/service.py` +
`api/server.py`) e frontend (`frontend/reports.js`) devem respeitá-lo. Fixtures de referência:
`tests/fixtures/reports_overview.json` e `tests/fixtures/reports_node.json`.

O formato de **nó** é definido em `src/studai/features/diagnostics/mastery.py` (docstring do módulo).

## Fontes de respostas

Todas as linhas respondidas de `attempt_questions` cujos `attempts` pertençam ao curso:

| `attempts.mode` | Origem | `kind` no payload |
|---|---|---|
| `static` / `adaptive` | Diagnóstico (`POST /api/diagnostics`) | `diagnostic` |
| `exam` | "Responder prova" persistido (`POST /api/exams/{id}/sessions`) | `exam` |

Cada tentativa com ao menos uma resposta é um ponto da linha do tempo, em ordem de
`created_at`. O domínio em cada ponto é **cumulativo** (todas as respostas até aquele ponto),
calculado com `mastery.compute_nodes(..., half_life_days=30, now=<instante do ponto>)`.

## `GET /api/reports/overview?courseId=<id>`

```jsonc
{
  "course": {"id", "name", "weights": {matéria: peso normalizado}, "non_assessable": {"Redação": 0.1667},
             "components": {matéria: {"f1", "d1", "d2"}}, "fase2": {...}},
  "kpis": {"estimated_score": 0-1, "estimated_score_delta": número|null, "answered": int, "sessions": int,
           "coverage": 0-1, "strong": int, "gaps": int, "last_activity": ISO|null},
  "timeline": [{"id", "kind": "diagnostic"|"exam", "label", "at": ISO, "answered", "accuracy", "estimated_score"}],
  "roots": [ids de matéria ordenados por peso do curso desc],
  "priorities": [até 10 ids de área/conteúdo/folha por prioridade desc],
  "nodes": {id: Nó + {"delta": número|null (vs. ponto anterior), "series": [mastery|null por ponto da timeline],
                      "children": [ids ordenados por prioridade desc]}},
  "latest_report": relatório do último diagnóstico finalizado (metrics.report) | null
}
```

* `coverage`: soma de `incidence × course_weight` das **áreas** com evidência / soma total das áreas.
* Sem respostas: `timeline: []`, KPIs zerados, nós `untested` (com incidência) — a tela mostra estado vazio.

## `GET /api/reports/nodes/<id>?courseId=<id>`

```jsonc
{
  "node": Nó (mesmo formato do overview),
  "series": [{"t": ISO, "attempt_id", "kind", "mastery"|null, "lower"|null, "upper"|null, "attempts"}],
  "children": [Nó],
  "recent_answers": [{"at", "attempt_id", "kind", "source_id", "number", "correct", "selected",
                      "correct_answer", "reason", "topic"}]   // até 20, mais recentes por último
}
```

Erros: curso ou nó desconhecido → 404 com `{"error": "..."}`.

## Persistência do "Responder prova"

* `POST /api/exams/<examId>/sessions` `{courseId}` → `{"id": attemptId, "questions": [{"ordinal", "number"}]}`
  Cria `attempts` com `mode='exam'` contendo as questões elegíveis daquela prova (mesmos filtros do pool).
* `POST /api/exam-sessions/<attemptId>/answers` `{ordinal, answer, reason?}` → igual ao diagnóstico:
  resposta incorreta exige `reason`; respostas corretas e em branco não recebem motivo.
* `POST /api/exam-sessions/<attemptId>/finish` → finaliza (status `completed`, grava `metrics_json`).

## Mapa do conhecimento (Fase 7)

Implementado em `features/reports/knowledge_map.py`; tela em `frontend/knowledge-map.js`. A profundidade e a
fórmula estão definidas em `src/studai/features/diagnostics/depth.py` (docstring do módulo).

Respostas: as mesmas dos relatórios **mais** as do estudo diário (`study_answers` das sessões do curso),
via `study.course_responses`, com `kind` `diagnostic` | `exam` | `daily`. Recência: `half_life_days=30`.
Os relatórios acima continuam sem as respostas diárias.

### `GET /api/knowledge-map?courseId=<id>`

```jsonc
{
  "course": {"id", "name", "weights": {matéria: peso normalizado}},
  "taxonomy_version": "fuvest-2027.v7",
  "kpis": {"nodes": int, "practiced": int (nós não-matéria com evidência),
           "depth": 0-1 (profundidade das matérias ponderada pelo curso), "depth_by_subject": {matéria: 0-1}},
  "nodes": [{"id", "name", "level", "parent_id", "subject", "attempts", "correct", "evidence", "mastery",
             "lower", "upper", "status", "course_weight", "incidence", "priority",
             "coverage", "depth", "children_total", "children_practiced",
             "questions": int (questões elegíveis do pool sob o nó)}],   // árvore viva inteira das matérias do curso
  "edges": [{"source": id do pai, "target": id do filho}]
}
```

### `GET /api/knowledge-map/nodes/<id>?courseId=<id>`

```jsonc
{
  "node": Nó (mesmo formato) + {"path": [nomes]},
  "ancestors": [ids da matéria até o pai],
  "children": [Nó] (filhos vivos, por incidência desc),
  "recent_answers": [{"at", "kind", "source_id", "number", "exam_label", "correct", "selected",
                      "correct_answer", "reason", "content_path"}],   // até 20, mais recentes por último
  "questions": [{"source_id", "number", "exam_label", "subject", "content_path", "statement", "answered"}]  // até 10, não respondidas primeiro
}
```

Erros: curso ou nó desconhecido → 404 com `{"error": "..."}`.
