"""카나리 자동 승격 판정 — 호출 N건의 오류율을 측정해 기준 통과 시 production을 승격합니다.

사용법:
    uv run python ops/06_auto_promote.py                     # 기본값: 20건, 오류율 10% 이하 시 승격
    uv run python ops/06_auto_promote.py --trials 30         # 호출 횟수 변경
    uv run python ops/06_auto_promote.py --threshold 0.05    # 허용 오류율 5%로 강화
    uv run python ops/06_auto_promote.py --dry-run           # 판정만 하고 실제 승격 안 함

동작 흐름:
    1. 현재 production과 DEFAULT 엔드포인트의 버전을 조회합니다.
    2. DEFAULT(최신 버전)에 N건을 호출해 오류율을 측정합니다.
    3. 오류율이 기준 이하면 production을 최신 버전으로 승격합니다.
    4. 오류율이 기준을 초과하면 승격을 중단하고 경고합니다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid

from _common import (
    control_client,
    data_client,
    get_runtime_arn,
    get_runtime_id,
    use_utf8_stdout,
)

# ---------------------------------------------------------------------------
# 테스트 프롬프트 세트 — 다양한 시나리오를 커버합니다.
# ---------------------------------------------------------------------------

TEST_PROMPTS = [
    "강남역 근처 이탈리안 추천해주세요. 1인 5만원 예산이에요.",
    "역삼역 한식 추천해주세요. 1인 7만원 예산이에요.",
    "삼성역 근처 중식 추천해주세요. 1인 4만원 예산이에요.",
    "트라토리아 벨라 내일 2명 예약해줘",
    "프렌치 레스토랑 예산 10만원으로 추천해주세요.",
    "선릉역 일식 추천해주세요. 1인 6만원 예산이에요.",
    "파이썬으로 퀵소트 짜줘",  # 주제 밖 — 거절해야 하지만 오류는 아님
    "스시 오마카세 히카리 3명 예약해줘",
    "강남역 근처 분위기 좋은 데이트 식당 추천해주세요.",
    "예산 3만원으로 강남역 근처 뭐 먹을 수 있어?",
]


def invoke_once(client, runtime_arn: str, qualifier: str, prompt: str) -> bool:
    """한 건을 호출하고 성공 여부를 반환합니다. True=성공, False=실패."""
    try:
        response = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            qualifier=qualifier,
            runtimeSessionId=f"canary-{uuid.uuid4().hex}",
            payload=json.dumps({"prompt": prompt}).encode("utf-8"),
        )
        body = response["response"].read().decode("utf-8")
        # 빈 응답도 실패로 간주
        return len(body.strip()) > 0
    except Exception as e:
        print(f"    [오류] {type(e).__name__}: {e}")
        return False


def get_live_version(client, runtime_id: str, endpoint_name: str) -> str | None:
    """엔드포인트의 현재 실행 버전을 반환합니다."""
    response = client.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
    for item in response.get("runtimeEndpoints", []):
        if item.get("name") == endpoint_name:
            return item.get("liveVersion")
    return None


def main() -> None:
    use_utf8_stdout()

    parser = argparse.ArgumentParser(description="카나리 자동 승격 판정")
    parser.add_argument("--trials", type=int, default=20, help="호출 횟수 (기본: 20)")
    parser.add_argument(
        "--threshold", type=float, default=0.10, help="허용 오류율 (기본: 0.10 = 10%%)"
    )
    parser.add_argument("--dry-run", action="store_true", help="판정만 하고 실제 승격 안 함")
    args = parser.parse_args()

    runtime_id = get_runtime_id()
    runtime_arn = get_runtime_arn()
    ctrl = control_client()
    data = data_client()

    # 1. 버전 상태 확인
    prod_version = get_live_version(ctrl, runtime_id, "production")
    default_version = get_live_version(ctrl, runtime_id, "DEFAULT")

    if prod_version is None:
        print("production 엔드포인트가 없습니다. 먼저 02_create_endpoint.py를 실행하세요.")
        sys.exit(1)

    print(f"현재 상태: production=V{prod_version}, DEFAULT=V{default_version}")

    if prod_version == default_version:
        print("production이 이미 최신 버전입니다. 승격할 필요가 없습니다.")
        return

    print(f"\n카나리 대상: DEFAULT(V{default_version})에 {args.trials}건 호출")
    print(f"허용 오류율: {args.threshold * 100:.0f}%")
    print("-" * 60)

    # 2. 카나리 호출
    successes = 0
    failures = 0

    for i in range(args.trials):
        prompt = TEST_PROMPTS[i % len(TEST_PROMPTS)]
        short_prompt = prompt[:30] + "..." if len(prompt) > 30 else prompt
        print(f"  [{i + 1:02d}/{args.trials}] {short_prompt}", end=" ")

        ok = invoke_once(data, runtime_arn, "DEFAULT", prompt)
        if ok:
            successes += 1
            print("OK")
        else:
            failures += 1
            print("FAIL")

        # API 쓰로틀 방지
        if i < args.trials - 1:
            time.sleep(1)

    # 3. 판정
    error_rate = failures / args.trials if args.trials > 0 else 0.0
    print("-" * 60)
    print(f"결과: {successes}건 성공, {failures}건 실패")
    print(f"오류율: {error_rate * 100:.1f}% (기준: {args.threshold * 100:.0f}%)")
    print()

    if error_rate > args.threshold:
        print("FAIL — 오류율이 기준을 초과했습니다. 승격을 중단합니다.")
        print("조치: 로그를 확인하고 문제를 수정한 뒤 재배포하세요.")
        sys.exit(1)

    print("PASS — 오류율 기준을 통과했습니다.")

    if args.dry_run:
        print(f"[dry-run] 실제 승격을 건너뜁니다. (V{prod_version} -> V{default_version})")
        return

    # 4. 승격 실행
    print(f"\nproduction을 V{prod_version} -> V{default_version}으로 승격합니다...")
    ctrl.update_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        endpointName="production",
        agentRuntimeVersion=default_version,
        description=f"auto-promoted to v{default_version} (error_rate={error_rate:.2%})",
    )
    print("승격 요청 완료. 상태가 READY로 전환될 때까지 대기합니다...")

    for _ in range(30):
        time.sleep(5)
        current = get_live_version(ctrl, runtime_id, "production")
        if current == default_version:
            print(f"승격 완료: production=V{current}")
            return

    print("타임아웃: 상태 전환이 완료되지 않았습니다. 수동으로 확인하세요.")
    print("확인: uv run python ops/01_list_versions.py")


if __name__ == "__main__":
    main()
