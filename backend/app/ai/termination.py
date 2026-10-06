from __future__ import annotations

from typing import Any

from ..config import settings


def evaluate(session: dict[str, Any], user_requested_end: bool = False) -> dict[str, Any]:
    turn_count = int(session.get("turn_count", 0))
    question_limit = min(int(session.get("question_limit", settings.max_turns)), settings.max_turns)
    if user_requested_end:
        return {
            "recommend_end": True,
            "reason": "user_requested",
            "can_end_now": True,
            "missing_topics": [],
        }
    if turn_count >= question_limit:
        return {
            "recommend_end": True,
            "reason": "question_limit_reached",
            "can_end_now": True,
            "missing_topics": [],
        }
    if turn_count >= settings.min_turns and turn_count >= question_limit - 1:
        return {
            "recommend_end": True,
            "reason": "near_question_limit",
            "can_end_now": False,
            "missing_topics": [],
        }
    return {
        "recommend_end": False,
        "reason": None,
        "can_end_now": False,
        "missing_topics": ["继续覆盖一个核心技能"],
    }
