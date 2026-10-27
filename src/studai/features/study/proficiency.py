"""Proficiência por matéria: confiança declarada, calibração e ritmo das respostas.

Complementa o domínio (``diagnostics.mastery``): separa o acerto com certeza do acerto
com dúvida e do chute, mede se a certeza declarada se confirma e compara o tempo médio
com o ritmo da 1ª fase (``scheduler.PACE_SECONDS``).
"""
from __future__ import annotations

from collections import defaultdict

from studai.features.study.scheduler import PACE_SECONDS

BUCKETS = ("sure", "doubt", "guess", "wrong")


def _bucket(response: dict) -> str | None:
    """Faixa da resposta; ``None`` para acertos antigos, sem confiança declarada."""
    if not response.get("is_correct", response.get("correct")):
        return "wrong"
    return response.get("confidence") or None


def _summary(responses: list[dict]) -> dict:
    counts = dict.fromkeys(BUCKETS, 0)
    sure_total = sure_correct = 0
    times = []
    for response in responses:
        bucket = _bucket(response)
        if bucket:
            counts[bucket] += 1
        if response.get("confidence") == "sure":
            sure_total += 1
            sure_correct += int(bool(response.get("is_correct", response.get("correct"))))
        if response.get("elapsed_ms") is not None:
            times.append(int(response["elapsed_ms"]) / 1000)
    rated = sum(counts.values())
    return {
        "answered": len(responses),
        "counts": counts,
        "shares": {bucket: round(count / rated, 4) if rated else 0.0 for bucket, count in counts.items()},
        # Calibração: quando o aluno diz "certeza", com que frequência acerta.
        "calibration": round(sure_correct / sure_total, 4) if sure_total else None,
        "avgSeconds": round(sum(times) / len(times), 1) if times else None,
        "overPace": sum(1 for seconds in times if seconds > PACE_SECONDS),
        "timed": len(times),
    }


def subject_proficiency(responses: list[dict]) -> dict:
    """Resumo geral e por matéria a partir de ``study.course_responses``.

    Cada resposta usa ``is_correct`` (acerto real, mesmo no chute), ``confidence``
    (``sure``/``doubt``/``guess`` ou vazio) e ``elapsed_ms``.
    """
    by_subject: dict[str, list[dict]] = defaultdict(list)
    for response in responses:
        if response.get("subject"):
            by_subject[response["subject"]].append(response)
    subjects = [{"subject": subject, **_summary(items)} for subject, items in by_subject.items()]
    subjects.sort(key=lambda item: (-item["answered"], item["subject"]))
    return {"paceSeconds": PACE_SECONDS, "overall": _summary(responses), "subjects": subjects}
