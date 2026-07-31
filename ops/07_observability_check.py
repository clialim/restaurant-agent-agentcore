"""관찰성 점검 — CloudWatch 알람 상태와 최근 런타임 메트릭을 읽어옵니다.

읽기 전용입니다. 리소스를 변경하지 않고 운영 상태를 요약합니다.
infra/observability/cloudwatch.yaml로 만든 알람과 AgentCore Runtime이
AWS/Bedrock-AgentCore 네임스페이스로 발행하는 메트릭을 확인합니다.

    uv run python ops/07_observability_check.py
    uv run python ops/07_observability_check.py --window-min 60

알람 상태가 하나라도 ALARM이면 종료 코드 1을 반환해, 스크립트를 모니터링
훅으로도 쓸 수 있습니다.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta

import boto3

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from _common import get_region, use_utf8_stdout  # noqa: E402

NAMESPACE = "AWS/Bedrock-AgentCore"
ALARM_PREFIX = "restaurant-agent-"

# (메트릭, 통계) — 최근 창의 런타임 상태 요약에 쓰는 지표
METRIC_QUERIES = [
    ("Invocations", "Sum"),
    ("SystemErrors", "Sum"),
    ("UserErrors", "Sum"),
    ("Throttles", "Sum"),
    ("Latency", "p99"),
]


def summarize_alarms(cw) -> list[dict]:
    """이 프로젝트가 만든 알람 상태를 반환합니다."""
    alarms: list[dict] = []
    paginator = cw.get_paginator("describe_alarms")
    for page in paginator.paginate(AlarmNamePrefix=ALARM_PREFIX):
        alarms.extend(page.get("MetricAlarms", []))
    return alarms


def recent_metric(cw, metric: str, stat: str, start, end) -> float | None:
    """최근 창의 메트릭 집계값을 반환합니다. 데이터가 없으면 None."""
    kwargs = {
        "Namespace": NAMESPACE,
        "MetricName": metric,
        "StartTime": start,
        "EndTime": end,
        "Period": 3600,
    }
    if stat.startswith("p"):
        kwargs["ExtendedStatistics"] = [stat]
    else:
        kwargs["Statistics"] = [stat]

    resp = cw.get_metric_statistics(**kwargs)
    points = resp.get("Datapoints", [])
    if not points:
        return None
    if stat.startswith("p"):
        return sum(p["ExtendedStatistics"][stat] for p in points) / len(points)
    return sum(p[stat] for p in points)


def main() -> int:
    use_utf8_stdout()

    parser = argparse.ArgumentParser(description="관찰성 점검(읽기 전용)")
    parser.add_argument(
        "--window-min", type=int, default=60, help="메트릭 조회 창(분, 기본: 60)"
    )
    args = parser.parse_args()

    region = get_region()
    cw = boto3.client("cloudwatch", region_name=region)

    end = datetime.now(UTC)
    start = end - timedelta(minutes=args.window_min)

    print("=" * 60)
    print(f"관찰성 점검 (region={region}, 최근 {args.window_min}분)")
    print("=" * 60)

    # 1. 알람 상태
    alarms = summarize_alarms(cw)
    print("\n[알람 상태]")
    if not alarms:
        print("  (프로젝트 알람 없음 — infra/observability/cloudwatch.yaml을 배포하세요)")
    in_alarm = [a for a in alarms if a["StateValue"] == "ALARM"]
    for a in alarms:
        print(f"  - {a['AlarmName']}: {a['StateValue']}")

    # 2. 최근 메트릭 요약
    print("\n[최근 런타임 메트릭]")
    for metric, stat in METRIC_QUERIES:
        value = recent_metric(cw, metric, stat, start, end)
        if value is None:
            print(f"  - {metric} ({stat}): 데이터 없음")
        elif metric == "Latency":
            print(f"  - {metric} ({stat}): {value:,.0f} ms")
        else:
            print(f"  - {metric} ({stat}): {value:,.0f}")

    # 3. 판정
    if in_alarm:
        names = ", ".join(a["AlarmName"] for a in in_alarm)
        print(f"\n경보 상태 알람: {names}")
        return 1
    print("\n모든 알람 정상(또는 알람 미배포).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
