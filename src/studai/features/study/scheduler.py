"""Agendamento de revisões com FSRS; único módulo que depende da biblioteca `fsrs`."""
from __future__ import annotations

from datetime import datetime

from fsrs import Card, Rating, Scheduler

# O estudo acontece em sessões diárias: sem passos de aprendizagem em minutos,
# todo cartão revisado volta em dias. Sem fuzz, o agendamento é reproduzível.
_SCHEDULER = Scheduler(learning_steps=(), relearning_steps=(), enable_fuzzing=False)


def new_card(now: datetime) -> dict:
    """Cria um cartão novo, vencido desde `now`."""
    card = Card(due=now)
    return card.to_dict()


# Ritmo da 1ª fase: 90 questões em 5 h ≈ 3,3 min por questão.
PACE_SECONDS = 200
RATING_LABELS = {Rating.Again: "again", Rating.Hard: "hard", Rating.Good: "good", Rating.Easy: "easy"}


def rating_for(correct: bool, confidence: str | None = None, elapsed_ms: int | None = None,
               hints_used: int = 0) -> Rating:
    """Nota do FSRS a partir do acerto, da confiança declarada, do tempo e das dicas.

    Erro, branco ou acerto no chute viram Again; acerto com dúvida ou com dica, Hard;
    acerto com certeza, Good, ou Easy dentro do ritmo da prova. Sem confiança (respostas
    antigas), acerto continua Good.
    """
    if not correct or confidence == "guess":
        return Rating.Again
    if confidence == "doubt" or hints_used:
        return Rating.Hard
    if confidence == "sure" and elapsed_ms is not None and elapsed_ms <= PACE_SECONDS * 1000:
        return Rating.Easy
    return Rating.Good


def rating_label(rating: Rating) -> str:
    return RATING_LABELS[rating]


def review(card_json: dict, correct: bool, at: datetime, *, confidence: str | None = None,
           elapsed_ms: int | None = None, hints_used: int = 0) -> tuple[dict, dict]:
    """Aplica uma revisão em `at` e devolve o cartão e o registro atualizados."""
    rating = rating_for(correct, confidence, elapsed_ms, hints_used)
    card, log = _SCHEDULER.review_card(Card.from_dict(card_json), rating, review_datetime=at)
    return card.to_dict(), log.to_dict()


def due_at(card_json: dict) -> datetime:
    return Card.from_dict(card_json).due


def retrievability(card_json: dict, at: datetime) -> float:
    """Probabilidade estimada de lembrar o conteúdo em `at` (0 para cartão nunca revisado)."""
    return float(_SCHEDULER.get_card_retrievability(Card.from_dict(card_json), current_datetime=at))


def is_due(card_json: dict, at: datetime) -> bool:
    return due_at(card_json) <= at
