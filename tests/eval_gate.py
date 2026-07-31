"""배포 게이트 — 품질과 보안을 모두 평가해 미달 시 exit 1로 배포를 차단합니다.

두 종류의 게이트를 적용합니다.
1. 품질 게이트: 3개 시나리오의 평균 점수가 임계값(0.7) 미만이면 차단.
2. 보안 게이트(fail-closed): 프롬프트 주입 등 보안 케이스는 평균과 무관하게
   개별 케이스가 임계값(1.0) 미만이면 즉시 차단.

보안은 평균으로 상쇄할 수 없으므로 개별 fail-closed로 다룹니다.
"""

import sys
from pathlib import Path

# 프로젝트 루트를 import 경로에 추가 — app.RestaurantAgent.main을 불러오기 위함
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strands import Agent
from strands.models import BedrockModel
from strands_evals import Case, Experiment
from strands_evals.evaluators import OutputEvaluator

from app.RestaurantAgent.main import (
    SYSTEM_PROMPT,
    check_reservations,
    create_reservation,
    get_restaurant_reviews,
    search_restaurants,
)
from tests.security_cases import SECURITY_CASES, SECURITY_RUBRIC, SECURITY_THRESHOLD

REGION = "us-west-2"
QUALITY_THRESHOLD = 0.7  # 품질 평균 게이트 — 미달 시 배포 차단


def get_response(case: Case) -> str:
    """배포될 코드와 같은 프롬프트·도구 구성으로 에이전트를 호출합니다."""
    agent = Agent(
        model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-6", region_name=REGION),
        system_prompt=SYSTEM_PROMPT,
        tools=[search_restaurants, get_restaurant_reviews, check_reservations, create_reservation],
        callback_handler=None,
    )
    return str(agent(case.input))


quality_cases = [
    Case[str, str](
        name="italian-recommend",
        input="강남역 근처 이탈리안 식당 추천해 주세요",
        expected_output="트라토리아 벨라를 추천하고 위치·가격 등 근거를 함께 안내합니다.",
        metadata={"category": "recommendation"},
    ),
    Case[str, str](
        name="no-spicy",
        input="매운 음식을 못 먹는데 강남 식당 추천해 주세요",
        expected_output=(
            "맵지 않은 식당만 추천합니다. "
            "매운맛 전문점인 매콤한 마라는 추천에 포함하지 않습니다."
        ),
        metadata={"category": "constraint"},
    ),
    Case[str, str](
        name="no-guessing",
        input="다음 주 공휴일에 영업하나요?",
        expected_output=(
            "확인되지 않은 정보를 추측하지 않고, "
            "지금은 확인이 불가하다고 안내하거나 확인 후 안내하겠다고 답합니다."
        ),
        metadata={"category": "grounding"},
    ),
]

quality_evaluator = OutputEvaluator(
    rubric="""
    Evaluate whether the actual output satisfies the expected behavior:

    1. REQUIRED FACTS: Restaurant names and reasons stated in the expected
       output must be present in the actual output.
    2. EXCLUSIONS: Items the expected output says to exclude must NOT
       appear as a recommendation.
    3. NO GUESSING: For unverifiable questions, the response must not state
       unconfirmed information as fact — it should say the information
       cannot be confirmed right now.

    Score 1.0 if the expected behavior is fully satisfied.
    Score 0.5 if partially satisfied.
    Score 0.0 if violated (wrong facts, excluded item recommended, or guessing).
    """,
    include_inputs=True,
)

security_evaluator = OutputEvaluator(rubric=SECURITY_RUBRIC, include_inputs=True)


def run_suite(title: str, cases: list[Case], evaluator: OutputEvaluator):
    """평가를 실행하고 (report, scores)를 출력하며 반환합니다."""
    report = Experiment[str, str](cases=cases, evaluators=[evaluator]).run_evaluations(
        get_response
    )
    print("=" * 60)
    print(title)
    print("=" * 60)
    for i, case in enumerate(report.cases):
        verdict = "PASS" if report.test_passes[i] else "FAIL"
        print(f"  [{verdict}] {case.get('name')}: {report.scores[i]:.2f}")
        print(f"         {report.reasons[i][:150]}")
    return report


def main() -> int:
    blocked = False

    # 1. 품질 게이트 — 평균 기반
    quality = run_suite("품질 평가 결과", quality_cases, quality_evaluator)
    quality_avg = sum(quality.scores) / len(quality.scores)
    print(f"\n품질 평균 점수: {quality_avg:.2f} (임계 {QUALITY_THRESHOLD})")
    if quality_avg < QUALITY_THRESHOLD:
        print("품질 게이트 미달 — 배포를 차단합니다.\n")
        blocked = True
    else:
        print("품질 게이트 통과.\n")

    # 2. 보안 게이트 — 개별 fail-closed
    security = run_suite("보안 평가 결과", SECURITY_CASES, security_evaluator)
    failed = [
        security.cases[i].get("name")
        for i, score in enumerate(security.scores)
        if score < SECURITY_THRESHOLD
    ]
    print(f"\n보안 임계: 각 케이스 >= {SECURITY_THRESHOLD} (fail-closed)")
    if failed:
        print(f"보안 게이트 미달 — 차단 케이스: {', '.join(failed)}")
        blocked = True
    else:
        print("보안 게이트 통과.")

    if blocked:
        print("\n배포를 차단합니다.")
        return 1
    print("\n모든 게이트 통과 — 배포를 진행합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
