"""도구·입력 검증 계층의 결정적 보안 단위 테스트.

LLM을 호출하지 않으므로 네트워크·비용 없이 빠르게 실행됩니다. CI에서
느린 LLM 평가 게이트보다 먼저 실행해, 입력 검증·예약 안전 통제의 회귀를
즉시(fail-fast) 차단하는 용도입니다.

    uv run pytest tests/test_tools_security.py -q
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.RestaurantAgent.main import (  # noqa: E402
    MAX_PROMPT_CHARS,
    create_reservation,
    validate_prompt,
)

# --- 입력 검증 (신뢰 경계) -------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "그냥 문자열",
        123,
        {},
        {"prompt": None},
        {"prompt": 123},
        {"prompt": "   "},
        {"prompt": ""},
    ],
)
def test_validate_prompt_rejects_invalid(payload):
    with pytest.raises(ValueError):
        validate_prompt(payload)


def test_validate_prompt_rejects_oversized():
    with pytest.raises(ValueError):
        validate_prompt({"prompt": "가" * (MAX_PROMPT_CHARS + 1)})


def test_validate_prompt_accepts_and_strips():
    assert validate_prompt({"prompt": "  강남 이탈리안 추천  "}) == "강남 이탈리안 추천"


def test_validate_prompt_accepts_boundary():
    text = "가" * MAX_PROMPT_CHARS
    assert validate_prompt({"prompt": text}) == text


# --- 예약 도구 안전 통제 ---------------------------------------------------


def test_reservation_rejects_unknown_restaurant():
    result = create_reservation("rest-999", _future(), 2)
    assert "예약 실패" in result


def test_reservation_rejects_bad_date_format():
    result = create_reservation("rest-001", "2025/01/01", 2)
    assert "예약 실패" in result
    assert "YYYY-MM-DD" in result


def test_reservation_rejects_past_date():
    past = (date.today() - timedelta(days=1)).isoformat()
    result = create_reservation("rest-001", past, 2)
    assert "예약 실패" in result
    assert "과거" in result


@pytest.mark.parametrize("party", [0, -3, 21, 1000])
def test_reservation_rejects_party_size_out_of_range(party):
    result = create_reservation("rest-001", _future(), party)
    assert "예약 실패" in result


def test_reservation_succeeds_on_valid_input():
    result = create_reservation("rest-001", _future(), 2)
    assert "예약 완료" in result
    assert "RSV-" in result


def _future() -> str:
    return (date.today() + timedelta(days=7)).isoformat()
