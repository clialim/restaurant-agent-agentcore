"""dining-coder Security Reviewer — 보안 전문 리뷰어 서브 에이전트.

품질 Reviewer(reviewer.py)와 별도로 보안 관점만 검토합니다. 코디네이터는
품질 Reviewer와 보안 Reviewer 모두 APPROVED를 줘야만 통과(합의 규칙)로 처리합니다.

이 패턴은 RestaurantAgent 프로젝트의 핵심 원칙 — "보안은 평균으로 상쇄할 수 없으므로
개별 fail-closed로 다룬다"(eval_gate.py)와 같은 결입니다.
"""

from __future__ import annotations

from strands import Agent, tool
from strands.models import BedrockModel
from tools import read_file

REGION = "us-west-2"
MODEL_ID = "us.anthropic.claude-sonnet-4-6"

SECURITY_REVIEWER_PROMPT = """당신은 RestaurantAgent 프로젝트의 보안 리뷰어입니다. read_file로 대상 파일을 읽고 다음 기준으로 검토합니다.

1. 입력 검증 — 모든 외부 입력(사용자, 도구 인자)이 타입·범위·형식을 fail-closed로 검증하는가.
   예외를 던지지 않고 명확한 실패 메시지를 반환하는가.
2. 경로 안전 — 파일 접근 시 resolve()+is_relative_to()로 경계를 검증하는가.
   사용자 입력을 경로에 그대로 넣지 않는가.
3. 명령 주입 — shell=True 사용 시 화이트리스트·타임아웃·파괴 패턴 차단이 있는가.
4. 정보 노출 — 시스템 내부 구현, 절대 경로, 자격증명이 오류 메시지에 노출되지 않는가.
5. 부작용 안전 — 상태를 변경하는 함수가 사전 확인 없이 실행되지 않는가(예약 생성 전 확인).

반드시 응답의 첫 줄을 APPROVED 또는 NEEDS_CHANGES로 시작하세요.
NEEDS_CHANGES면 고칠 항목을 번호로 나열하고, 각 항목에 위협 시나리오를 한 줄씩 붙이세요.
"""

security_reviewer = Agent(
    model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
    system_prompt=SECURITY_REVIEWER_PROMPT,
    tools=[read_file],
    callback_handler=None,
)


@tool
def security_review_code(path: str) -> str:
    """지정한 파일의 보안을 검토하고 판정을 반환합니다.

    Args:
        path: workspace 기준 검토 대상 파일 경로.
    """
    return str(security_reviewer(f"{path} 파일의 보안을 검토해 주세요."))
