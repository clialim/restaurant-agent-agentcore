"""qualifier로 엔드포인트를 지정해 에이전트를 호출합니다.

사용법:
    uv run python ops/03_invoke_endpoint.py
    uv run python ops/03_invoke_endpoint.py production
    uv run python ops/03_invoke_endpoint.py production "강남역 근처 한식 추천해주세요. 예산 7만원이에요."
"""

import json
import sys
import uuid

from _common import data_client, get_runtime_arn, use_utf8_stdout

DEFAULT_QUALIFIER = "DEFAULT"
DEFAULT_PROMPT = "강남역 근처 이탈리안 추천해주세요. 1인 5만원 예산이에요."


def main() -> None:
    use_utf8_stdout()
    qualifier = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUALIFIER
    prompt = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PROMPT

    runtime_arn = get_runtime_arn()
    client = data_client()

    print(f"엔드포인트: {qualifier}")
    print(f"질문: {prompt}\n")

    response = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier=qualifier,
        # runtimeSessionId은 33자 이상이어야 합니다.
        runtimeSessionId=f"ops-{uuid.uuid4().hex}",
        payload=json.dumps({"prompt": prompt}).encode("utf-8"),
    )

    body = response["response"].read().decode("utf-8")
    try:
        parsed = json.loads(body)
        print(parsed.get("result", parsed))
    except json.JSONDecodeError:
        print(body)


if __name__ == "__main__":
    main()
