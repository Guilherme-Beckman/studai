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
* `POST /api/exam-sessions/<attemptId>/answers` `{ordinal, answer, reason?}` → igual ao diagnóstico, mas o
  motivo é opcional (erro sem motivo grava `error_reason = null`).
* `POST /api/exam-sessions/<attemptId>/finish` → finaliza (status `completed`, grava `metrics_json`).
