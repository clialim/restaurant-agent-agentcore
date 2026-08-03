"""dining-coder Tester — 테스트 작성·실행 전용 서브 에이전트.

Tester에게는 write_file과 run_shell만 부여합니다(권한 분리). 결과의 마지막 줄을
반드시 passed=N failed=M 형식으로 출력하도록 강제합니다 — 이 고정된 형식이 코디네이터가
통과 여부를 판정하는 유일한 근거입니다. 실패 시에는 stderr를 포함한 원문을 함께 돌려줘,
코디네이터가 요약 없이 그대로 Coder에게 전달할 수 있게 합니다.
"""

from __future__ import annotations

import os

from strands import Agent, tool
from strands.models import BedrockModel
from tools import run_shell, write_file

REGION = "us-west-2"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)

TESTER_PROMPT = """당신은 RestaurantAgent 프로젝트의 테스트 엔지니어입니다.

검증 대상 함수의 pytest 테스트를 작성해 workspace 안 test_<대상이름>.py 파일에 저장하고 실행합니다.
- 경계값, 잘못된 입력, 정상 케이스를 모두 포함하세요.
- 테스트 실행은 run_shell("python -m pytest <테스트파일> -q")로 합니다.
- 파일 경로는 workspace 루트 기준 상대 경로를 사용하세요(예: test_reservation.py).

응답의 마지막 줄은 반드시 다음 형식으로 출력하세요.
passed=N failed=M
실패한 테스트가 있으면 그 위에 실패 로그(stderr 포함)를 원문 그대로 함께 적으세요.
"""

tester = Agent(
    model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
    system_prompt=TESTER_PROMPT,
    tools=[write_file, run_shell],
    callback_handler=None,
)


@tool
def run_tests(path: str) -> str:
    """대상 파일의 함수에 대한 테스트를 작성·실행하고 결과를 반환합니다.

    Args:
        path: workspace 기준 테스트 대상 파일 경로.
    """
    return str(tester(f"{path} 파일의 함수를 테스트해 주세요."))
