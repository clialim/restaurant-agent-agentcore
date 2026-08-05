"""내부 추론 필터의 fail-closed 동작을 검증하는 결정적 테스트.

배포되는 RestaurantAgent 진입점과 동일한 strip_thinking을 대상으로,
완결 블록 제거·잘린 추론 차단·정상 답변 보존을 확인합니다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.RestaurantAgent.main import strip_thinking


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 완결된 블록은 제거하고 주변 답변은 유지합니다.
        ("<thinking>내부 추론</thinking>안녕하세요", "안녕하세요"),
        ("앞<thinking>가운데</thinking>뒤", "앞뒤"),
        ("<thinking>a</thinking>중간<thinking>b</thinking>끝", "중간끝"),
        # 추론 태그가 없으면 원문을 그대로 유지합니다.
        ("정상 응답입니다.", "정상 응답입니다."),
        # 대소문자와 공백 변형도 처리합니다.
        ("<THINKING>대문자</THINKING>답변", "답변"),
        ("< thinking >공백</ thinking >끝", "끝"),
    ],
)
def test_complete_and_normal(raw: str, expected: str) -> None:
    assert strip_thinking(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "<thinking>잘린 내부 추론 노출 위험",
        "<thinking>credential=abc 비밀 계획",
        "보이는 답변<thinking>여기부터 잘린 추론",
        "<thinking>1</thinking>답변<thinking>이어지다 잘림",
    ],
)
def test_unclosed_opener_drops_tail(raw: str) -> None:
    """닫히지 않은 여는 태그 이후는 fail-closed로 모두 제거합니다."""
    cleaned = strip_thinking(raw)
    assert "<thinking" not in cleaned.lower()
    assert "잘린" not in cleaned
    assert "credential" not in cleaned
    assert "여기부터" not in cleaned


def test_unclosed_opener_preserves_prefix() -> None:
    assert strip_thinking("보이는 답변<thinking>잘린 추론") == "보이는 답변"


def test_stray_close_tag_preserves_answer() -> None:
    """순서대로 조립된 응답에서 여는 태그 없는 닫는 태그는 정상 텍스트로 봅니다."""
    assert strip_thinking("중요한 답변 </thinking> 이어지는 답변") == "중요한 답변  이어지는 답변"


def test_no_reasoning_leaks_after_open() -> None:
    assert "secret" not in strip_thinking("답<thinking>secret reasoning")
