"""dining-coder 코디네이터 — Coder·Reviewer·Tester 자기 교정 루프.

네 에이전트(작성·품질 검토·보안 검토·테스트)를 LLM이 아니라 파이썬 코드가 조율합니다.

이 패턴의 네 가지 작동 조건:
1. 루프 카운터·종료 조건은 코드가 판정합니다. "3회까지"를 프롬프트로 지시하면 모델이
   셈을 놓쳐 무한히 돌거나 1회로 끝냅니다. range(1, MAX_ROUNDS + 1)로 재현성을 확보합니다.
2. 판정 형식을 고정합니다. Reviewer의 APPROVED/NEEDS_CHANGES 판정과 Tester의
   passed=N failed=M이 없으면 통과 여부를 문자열에서 추측해야 합니다.
3. 권한을 분리합니다. Coder만 쓰기, Reviewer는 읽기, Tester는 쓰기+실행. Reviewer가 직접
   고치면 Coder에게 갈 학습 신호가 사라집니다.
4. 근거는 원문으로 전달합니다. Reviewer 지적과 Tester stderr를 요약하면 Coder가 같은
   실수를 반복합니다.
"""

from __future__ import annotations

import logging
import re

from coder import build_agent
from reviewer import review_code
from security_reviewer import security_review_code
from tester import run_tests
from tools import write_file

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3
THINKING_PATTERN = re.compile(r"<thinking>.*?</thinking>\s*", re.DOTALL)
TEST_SUMMARY_PATTERN = re.compile(r"^passed=(\d+)\s+failed=(\d+)$", re.MULTILINE)
DEMO_DRAFT = '''def is_over_capacity(reservation_count, restaurant_capacity):
    """예약 인원이 수용 인원을 넘는지 확인합니다."""
    return reservation_count > restaurant_capacity
'''

# Coder는 라운드 간 대화 맥락을 유지하도록 한 번만 생성합니다(피드백도 원문으로 함께 전달).
coder = build_agent()


def write_code(prompt: str) -> str:
    """Coder에게 코드 작성을 요청합니다."""
    return str(coder(prompt))


def _final_answer(output: str) -> str:
    """모델 내부 추론 태그를 제거하고 판정에 사용할 최종 답변만 반환합니다."""
    return THINKING_PATTERN.sub("", output).strip()


def _approved(review: str) -> bool:
    """판정 토큰을 찾되 NEEDS_CHANGES가 하나라도 있으면 fail-closed 처리합니다."""
    verdicts = re.findall(
        r"(?:^|>)\s*(APPROVED|NEEDS_CHANGES)\b",
        review,
        flags=re.MULTILINE,
    )
    return bool(verdicts) and "NEEDS_CHANGES" not in verdicts


def _passed(test_output: str) -> tuple[bool, str]:
    """Tester 출력에서 마지막 passed=N failed=M 판정 행을 찾습니다."""
    matches = list(TEST_SUMMARY_PATTERN.finditer(test_output))
    if not matches:
        last_line = test_output.split("\n")[-1].strip() if test_output else "판정 형식 없음"
        return False, last_line
    match = matches[-1]
    passed = int(match.group(1))
    failed = int(match.group(2))
    return (passed > 0 and failed == 0, match.group(0))


def build_with_review(
    task: str,
    target: str = "reservation.py",
    initial_draft: str | None = None,
) -> dict:
    """자기 교정 루프 — 최대 MAX_ROUNDS회 재작업.

    Args:
        task: 코드 작성 요청(자연어).
        target: 작성·검토·테스트 대상 파일의 workspace 기준 상대 경로.
        initial_draft: 첫 라운드부터 검토할 기존 초안. 없으면 Coder가 새로 작성합니다.

    Returns:
        state("APPROVED" 또는 "BEST_EFFORT"), rounds, code, history를 담은 dict.
    """
    logger.info("=== 코디네이터 시작: %s (target=%s) ===", task, target)
    feedback = ""
    code = ""
    history: list[dict] = []

    for round_no in range(1, MAX_ROUNDS + 1):
        if round_no == 1 and initial_draft is not None:
            write_file(target, initial_draft)
            code = initial_draft
            logger.info("round=%s Coder 초기 초안 완료 (재작업 유도)", round_no)
        else:
            prompt = f"{task}\n\n결과 코드는 workspace 루트 기준 상대 경로 '{target}'에 작성해 주세요."
            if feedback:
                prompt += (
                    "\n\n재작업입니다. 아래 실패 항목을 하나도 빠뜨리지 말고 실제 대상 파일에 반영하세요. "
                    "기존 파일을 읽고 수정본으로 덮어쓴 뒤 run_shell로 검증해야 합니다. "
                    "설명만 반환하거나 일부 항목만 수정하지 마세요.\n\n"
                    f"지난 회차 지적 사항 (원문 그대로):\n{feedback}"
                )
            code = _final_answer(write_code(prompt))
            logger.info("round=%s Coder 완료", round_no)

        review = _final_answer(str(review_code(target)))
        approved = _approved(review)
        logger.info("round=%s quality_approved=%s", round_no, approved)

        sec_review = _final_answer(str(security_review_code(target)))
        sec_approved = _approved(sec_review)
        logger.info("round=%s security_approved=%s", round_no, sec_approved)

        # 합의 규칙: 품질·보안 리뷰어 모두 APPROVED여야 통과합니다.
        both_approved = approved and sec_approved

        test_output = _final_answer(str(run_tests(target)))
        passed, last_line = _passed(test_output)
        logger.info("round=%s passed=%s (%s)", round_no, passed, last_line)

        history.append(
            {
                "round": round_no,
                "approved": both_approved,
                "quality_approved": approved,
                "security_approved": sec_approved,
                "quality_review": review,
                "security_review": sec_review,
                "passed": passed,
                "test": last_line,
            }
        )

        # 품질·보안·테스트 세 판정 모두 통과해야 완료입니다.
        if both_approved and passed:
            return {
                "state": "APPROVED",
                "rounds": round_no,
                "code": code,
                "history": history,
            }

        # 미통과 — 실패 근거만 요약 없이 원문으로 다음 회차 Coder에게 전달합니다.
        feedback_parts = []
        if not approved:
            feedback_parts.append(f"[품질 리뷰 판정]\n{review}")
        if not sec_approved:
            feedback_parts.append(f"[보안 리뷰 판정]\n{sec_review}")
        if not passed:
            feedback_parts.append(f"[테스트 결과]\n{test_output}")
        feedback = "\n\n".join(feedback_parts)

    # MAX_ROUNDS 초과 — 무한 루프 없이 최선 결과를 반환합니다.
    return {
        "state": "BEST_EFFORT",
        "rounds": MAX_ROUNDS,
        "code": code,
        "history": history,
        "remaining": feedback,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # 촬영용 시나리오: 결함 있는 기존 초안을 Reviewer 피드백으로 수정합니다.
    result = build_with_review(
        "예약 인원이 식당 수용 인원을 넘는지 안전하게 판정하는 도구를 완성해 주세요.",
        initial_draft=DEMO_DRAFT,
    )
    print(f"최종 상태: {result['state']} (라운드: {result['rounds']})")
