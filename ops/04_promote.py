"""카나리 검증 후 production 엔드포인트를 승격합니다.

사용법:
    uv run python ops/04_promote.py            # 최신 버전으로 승격
    uv run python ops/04_promote.py 3          # V3으로 승격
    uv run python ops/04_promote.py 3 canary   # 대상 엔드포인트 지정
"""

import sys

from _common import control_client, get_runtime_id, use_utf8_stdout

DEFAULT_ENDPOINT_NAME = "production"


def current_version(client, runtime_id: str, endpoint_name: str) -> str | None:
    """엔드포인트가 현재 서비스하는 버전을 반환합니다."""
    response = client.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
    for item in response.get("runtimeEndpoints", []):
        if item.get("name") == endpoint_name:
            return item.get("liveVersion")
    return None


def latest_version(client, runtime_id: str) -> str:
    """가장 높은 버전 번호를 반환합니다."""
    response = client.list_agent_runtime_versions(agentRuntimeId=runtime_id)
    versions = [
        item["agentRuntimeVersion"]
        for item in response.get("agentRuntimes", [])
        if item.get("agentRuntimeVersion")
    ]
    if not versions:
        raise RuntimeError("조회된 버전이 없습니다.")
    return max(versions, key=int)


def main() -> None:
    use_utf8_stdout()
    version = sys.argv[1] if len(sys.argv) > 1 else None
    endpoint_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ENDPOINT_NAME

    runtime_id = get_runtime_id()
    client = control_client()

    before = current_version(client, runtime_id, endpoint_name)
    if before is None:
        raise SystemExit(
            f"'{endpoint_name}' 엔드포인트가 없습니다. "
            "먼저 ops/02_create_endpoint.py로 생성하세요."
        )

    target_version = version or latest_version(client, runtime_id)

    if before == target_version:
        print(f"{endpoint_name}은(는) 이미 V{target_version}을 가리키고 있습니다.")
        return

    response = client.update_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        endpointName=endpoint_name,
        agentRuntimeVersion=target_version,
        description=f"V{target_version}으로 승격",
    )

    print(f"승격 요청: {endpoint_name} V{before} -> V{target_version}")
    print(f"  상태: {response.get('status')}")
    print("\n확인: uv run python ops/01_list_versions.py")


if __name__ == "__main__":
    main()
