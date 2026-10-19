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


def rating_for(correct: bool) -> Rating:
    """Acerto vira Good; erro ou branco vira Again."""
    return Rating.Good if correct else Rating.Again


def review(card_json: dict, correct: bool, at: datetime) -> tuple[dict, dict]:
    """Aplica uma revisão em `at` e devolve o cartão e o registro atualizados."""
    card, log = _SCHEDULER.review_card(Card.from_dict(card_json), rating_for(correct), review_datetime=at)
    return card.to_dict(), log.to_dict()


def due_at(card_json: dict) -> datetime:
    return Card.from_dict(card_json).due


def retrievability(card_json: dict, at: datetime) -> float:
    """Probabilidade estimada de lembrar o conteúdo em `at` (0 para cartão nunca revisado)."""
    return float(_SCHEDULER.get_card_retrievability(Card.from_dict(card_json), current_datetime=at))


def is_due(card_json: dict, at: datetime) -> bool:
    return due_at(card_json) <= at
