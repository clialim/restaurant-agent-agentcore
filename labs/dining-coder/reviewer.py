"""dining-coder Reviewer — 코드 품질 판정 전용 서브 에이전트.

권한 분리가 이 패턴의 작동 조건입니다. Reviewer에게는 read_file만 부여합니다.
쓰기 도구를 주면 지적 대신 직접 고쳐버려 Coder에게 갈 학습 신호가 사라집니다.

판정은 반드시 첫 줄을 APPROVED 또는 NEEDS_CHANGES로 시작하도록 강제합니다 —
이 고정된 형식이 코디네이터 자동화의 전제입니다.
"""

from __future__ import annotations

import os

from strands import Agent, tool
from strands.models import BedrockModel
from tools import read_file

REGION = "us-west-2"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)

REVIEWER_PROMPT = """당신은 RestaurantAgent 프로젝트의 코드 리뷰어입니다. read_file로 대상 파일을 읽고 다음 기준으로 검토합니다.

1. 경계값 처리 — 수용 인원과 정확히 같은 인원, 0명, 최소·최대 경계 등을 올바르게 다루는가.
2. 잘못된 입력 처리 — 음수, 형식 오류, 존재하지 않는 대상을 예외가 아니라 명확한 실패 메시지로 되돌리는가(fail-closed).
3. 프로젝트 컨벤션 — @tool 데코레이터, 한국어 docstring의 Args, 존댓말, 단일 책임 함수 분리.
4. 이름의 명확성 — 함수·변수 이름이 의도를 드러내는가.

반드시 응답의 첫 줄을 APPROVED 또는 NEEDS_CHANGES로 시작하세요.
NEEDS_CHANGES면 고칠 항목을 번호로 나열하고, 각 항목에 근거를 한 줄씩 붙이세요.
"""

reviewer = Agent(
    model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
    system_prompt=REVIEWER_PROMPT,
    tools=[read_file],
    callback_handler=None,
)


@tool
def review_code(path: str) -> str:
    """지정한 파일의 코드 품질을 검토하고 판정을 반환합니다.

    Args:
        path: workspace 기준 검토 대상 파일 경로.
    """
    return str(reviewer(f"{path} 파일의 코드를 검토해 주세요."))
