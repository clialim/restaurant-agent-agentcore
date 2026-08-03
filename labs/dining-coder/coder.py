"""dining-coder — RestaurantAgent 개발을 돕는 코딩 에이전트.

「코딩 에이전트 만들기」 미션을 이 프로젝트 맥락으로 재구성했습니다. 범용 코딩 데모가
아니라, RestaurantAgent(강남 식당 추천 에이전트)의 코드 컨벤션을 따르는 도구·테스트·
검증 코드를 workspace 샌드박스 안에서 작성·실행·자가수정하는 에이전트입니다.

신뢰 경계:
- 도구는 tools.py의 run_shell·read_file·write_file(모두 workspace 경계 안)만 사용합니다.
- 에이전트는 실제 프로젝트 소스를 직접 수정하지 않습니다. 사람이 workspace 산출물을
  검토한 뒤 프로젝트에 반영합니다.
- AfterToolCallEvent 훅으로 모든 도구 호출을 workspace/.tool-log.jsonl에 감사 로그로
  남깁니다(도구명·인자·오류). 이는 프로젝트의 관찰성·감사 원칙과 같은 결입니다.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime

from strands import Agent
from strands.hooks import HookProvider, HookRegistry
from strands.hooks.events import AfterToolCallEvent
from strands.models import BedrockModel
from tools import WORKSPACE, read_file, run_shell, write_file

REGION = "us-west-2"
DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
TOOL_LOG = WORKSPACE / ".tool-log.jsonl"

SYSTEM_PROMPT = """당신은 RestaurantAgent 프로젝트(강남 식당 추천 AI 에이전트)의 개발을 돕는 코딩 에이전트입니다.

## 작업 환경
- 모든 파일 읽기·쓰기·실행은 workspace/ 안에서만 수행합니다. 실제 프로젝트 소스는 직접 수정하지 않습니다.
- 사람이 workspace 산출물을 검토한 뒤 프로젝트에 반영합니다.

## 프로젝트 코드 컨벤션 (반드시 따를 것)
- 에이전트 도구는 Strands의 @tool 데코레이터로 정의하고, 한국어 docstring에 Args를 상세히 적습니다.
- 부작용이 있거나 외부 입력을 받는 함수는 fail-closed로 입력을 검증합니다(타입·범위·형식).
  잘못된 입력은 예외를 던지지 말고 명확한 실패 메시지 문자열을 반환합니다.
- 사용자에게 보이는 문자열은 존댓말 한국어로 작성합니다.
- Python 3.13 문법을 사용하고, 표준 라이브러리를 우선합니다.

## 작업 원칙
1. 파일을 작성한 뒤에는 반드시 run_shell로 실행하거나 pytest로 테스트해 결과를 확인합니다.
2. 실행이 실패하면 stderr에서 원인을 찾아 코드를 수정하고 다시 실행합니다.
3. 실행 성공이 확인될 때까지 완료를 선언하지 않습니다.
4. 허용되지 않은 명령이나 workspace 밖 경로로 거부당하면, 사유를 읽고 허용된 범위 안에서 다른 방법을 찾습니다.
"""


class ToolAuditHook(HookProvider):
    """모든 도구 호출을 workspace/.tool-log.jsonl에 감사 로그로 남깁니다.

    도구가 예외로 실패하더라도(AfterToolCallEvent.exception) 호출 기록이 남도록 해,
    에이전트의 행동을 사후에 추적할 수 있게 합니다.
    """

    def register_hooks(self, registry: HookRegistry) -> None:
        """훅 콜백을 등록합니다."""
        registry.add_callback(AfterToolCallEvent, self._log)

    def _log(self, event: AfterToolCallEvent) -> None:
        tool_use = event.tool_use or {}
        entry = {
            "ts": datetime.now(UTC).isoformat(),
            "tool": tool_use.get("name"),
            "input": tool_use.get("input"),
            "error": str(event.exception) if event.exception else None,
        }
        with TOOL_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _use_utf8_stdout() -> None:
    """Windows 콘솔(cp949)에서 이모지·비ASCII 출력 시 UnicodeEncodeError를 방지합니다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def build_agent() -> Agent:
    """RestaurantAgent 개발용 코딩 에이전트를 생성합니다."""
    return Agent(
        model=BedrockModel(model_id=MODEL_ID, region_name=REGION),
        system_prompt=SYSTEM_PROMPT,
        tools=[run_shell, read_file, write_file],
        hooks=[ToolAuditHook()],
        # 스트리밍 중간 결과를 콘솔로 출력하지 않음(Windows 인코딩 이슈 회피).
        callback_handler=None,
    )


if __name__ == "__main__":
    _use_utf8_stdout()
    agent = build_agent()
    result = agent(
        "RestaurantAgent의 @tool 컨벤션을 따르는 새 도구 get_restaurant_hours를 "
        "workspace/get_restaurant_hours.py에 작성해 주세요. 식당 이름을 받아 영업시간을 "
        "돌려주되, 등록되지 않은 식당은 fail-closed로 명확한 실패 메시지를 반환해야 합니다. "
        "작성 후 pytest로 정상·실패 경로를 모두 검증해 주세요."
    )
    print(str(result))
