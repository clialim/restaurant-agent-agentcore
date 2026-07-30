"""ops 스크립트 공용 유틸 — 배포 상태에서 런타임 정보를 읽어옵니다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOYED_STATE = PROJECT_ROOT / "agentcore" / ".cli" / "deployed-state.json"
AWS_TARGETS = PROJECT_ROOT / "agentcore" / "aws-targets.json"

DEFAULT_TARGET = "default"
RUNTIME_NAME = "RestaurantAgent"


def get_region(target: str = DEFAULT_TARGET) -> str:
    """aws-targets.json에서 배포 대상 리전을 읽습니다."""
    if not AWS_TARGETS.exists():
        raise FileNotFoundError(f"배포 대상 파일을 찾을 수 없습니다: {AWS_TARGETS}")

    targets = json.loads(AWS_TARGETS.read_text(encoding="utf-8"))
    for entry in targets:
        if entry.get("name") == target:
            return entry["region"]

    raise ValueError(f"'{target}' 대상을 aws-targets.json에서 찾을 수 없습니다.")


def _get_runtime(runtime_name: str, target: str) -> dict:
    """deployed-state.json에서 배포된 런타임 정보를 읽습니다."""
    if not DEPLOYED_STATE.exists():
        raise FileNotFoundError(
            f"배포 상태 파일이 없습니다: {DEPLOYED_STATE}\n"
            "먼저 'agentcore deploy'를 실행하세요."
        )

    state = json.loads(DEPLOYED_STATE.read_text(encoding="utf-8"))
    runtimes = (
        state.get("targets", {})
        .get(target, {})
        .get("resources", {})
        .get("runtimes", {})
    )

    runtime = runtimes.get(runtime_name)
    if not runtime:
        available = ", ".join(runtimes) or "(없음)"
        raise ValueError(
            f"'{runtime_name}' 런타임을 찾을 수 없습니다. 배포된 런타임: {available}"
        )

    return runtime


def get_runtime_id(runtime_name: str = RUNTIME_NAME, target: str = DEFAULT_TARGET) -> str:
    """배포된 런타임 ID를 반환합니다."""
    return _get_runtime(runtime_name, target)["runtimeId"]


def get_runtime_arn(runtime_name: str = RUNTIME_NAME, target: str = DEFAULT_TARGET) -> str:
    """배포된 런타임 ARN을 반환합니다."""
    return _get_runtime(runtime_name, target)["runtimeArn"]


def use_utf8_stdout() -> None:
    """Windows 콘솔(cp949)에서 이모지 출력 시 UnicodeEncodeError를 방지합니다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def control_client(target: str = DEFAULT_TARGET):
    """AgentCore 제어 평면 클라이언트를 생성합니다."""
    return boto3.client("bedrock-agentcore-control", region_name=get_region(target))


def data_client(target: str = DEFAULT_TARGET):
    """AgentCore 데이터 평면(호출) 클라이언트를 생성합니다."""
    return boto3.client("bedrock-agentcore", region_name=get_region(target))
