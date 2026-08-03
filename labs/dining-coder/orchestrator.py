"""dining-coder 코디네이터 — Coder·Reviewer·Tester 자기 교정 루프.

세 에이전트(작성·검토·테스트)를 LLM이 아니라 파이썬 코드가 조율합니다.

이 패턴의 네 가지 작동 조건:
1. 루프 카운터·종료 조건은 코드가 판정합니다. "3회까지"를 프롬프트로 지시하면 모델이
   셈을 놓쳐 무한히 돌거나 1회로 끝냅니다. range(1, MAX_ROUNDS + 1)로 재현성을 확보합니다.
2. 판정 형식을 고정합니다. Reviewer의 APPROVED/NEEDS_CHANGES 접두어와 Tester의 마지막 줄
   passed=N failed=M이 없으면 통과 여부를 문자열에서 추측해야 합니다.
3. 권한을 분리합니다. Coder만 쓰기, Reviewer는 읽기, Tester는 쓰기+실행. Reviewer가 직접
   고치면 Coder에게 갈 학습 신호가 사라집니다.
4. 근거는 원문으로 전달합니다. Reviewer 지적과 Tester stderr를 요약하면 Coder가 같은
   실수를 반복합니다.
"""

from __future__ import annotations

import logging

from coder import build_agent
from reviewer import review_code
from tester import run_tests

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3

# Coder는 라운드 간 대화 맥락을 유지하도록 한 번만 생성합니다(피드백도 원문으로 함께 전달).
coder = build_agent()


def write_code(prompt: str) -> str:
    """Coder에게 코드 작성을 요청합니다."""
    return str(coder(prompt))


def _passed(test_output: str) -> tuple[bool, str]:
    """Tester 출력의 마지막 줄에서 통과 여부를 판정합니다."""
    last_line = test_output.split("\n")[-1].strip() if test_output else ""
    return ("failed=0" in last_line, last_line)


def build_with_review(task: str, target: str = "reservation.py") -> dict:
    """자기 교정 루프 — 최대 MAX_ROUNDS회 재작업.

    Args:
        task: 코드 작성 요청(자연어).
        target: 작성·검토·테스트 대상 파일의 workspace 기준 상대 경로.

    Returns:
        state("APPROVED" 또는 "BEST_EFFORT"), rounds, code, history를 담은 dict.
    """
    logger.info("=== 코디네이터 시작: %s (target=%s) ===", task, target)
    feedback = ""
    code = ""
    history: list[dict] = []

    for round_no in range(1, MAX_ROUNDS + 1):
        prompt = f"{task}\n\n결과 코드는 workspace 루트 기준 상대 경로 '{target}'에 작성해 주세요."
        if feedback:
            prompt += f"\n\n지난 회차 지적 사항 (원문 그대로):\n{feedback}"
        code = write_code(prompt)
        logger.info("round=%s Coder 완료", round_no)

        review = str(review_code(target)).strip()
        approved = review.startswith("APPROVED")
        logger.info("round=%s approved=%s", round_no, approved)

        test_output = str(run_tests(target)).strip()
        passed, last_line = _passed(test_output)
        logger.info("round=%s passed=%s (%s)", round_no, passed, last_line)

        history.append(
            {
                "round": round_no,
                "approved": approved,
                "passed": passed,
                "review": review,
                "test": last_line,
            }
        )

        # 두 판정 모두 통과해야 완료 — 어느 하나라도 미통과면 재작업합니다.
        if approved and passed:
            return {
                "state": "APPROVED",
                "rounds": round_no,
                "code": code,
                "history": history,
            }

        # 미통과 — 근거를 요약 없이 원문으로 다음 회차 Coder에게 전달합니다.
        feedback = f"[리뷰 판정]\n{review}\n\n[테스트 결과]\n{test_output}"

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
    # 일부러 부족한 요구로 재작업 루프를 유도합니다.
    result = build_with_review("예약 인원이 식당 수용 인원을 넘는지만 보면 됩니다")
    print(f"최종 상태: {result['state']} (라운드: {result['rounds']})")
