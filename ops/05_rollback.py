"""엔드포인트를 이전 버전으로 롤백합니다.

사용법:
    uv run python ops/05_rollback.py            # 직전 버전으로 롤백
    uv run python ops/05_rollback.py 1          # V1로 롤백
    uv run python ops/05_rollback.py 1 canary   # 대상 엔드포인트 지정
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


def previous_version(client, runtime_id: str, current: str) -> str:
    """현재 버전보다 낮은 버전 중 가장 높은 값을 반환합니다."""
    response = client.list_agent_runtime_versions(agentRuntimeId=runtime_id)
    candidates = [
        item["agentRuntimeVersion"]
        for item in response.get("agentRuntimes", [])
        if item.get("agentRuntimeVersion")
        and int(item["agentRuntimeVersion"]) < int(current)
    ]
    if not candidates:
        raise SystemExit(f"V{current}보다 이전 버전이 없어 롤백할 수 없습니다.")
    return max(candidates, key=int)


def main() -> None:
    use_utf8_stdout()
    version = sys.argv[1] if len(sys.argv) > 1 else None
    endpoint_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ENDPOINT_NAME

    runtime_id = get_runtime_id()
    client = control_client()

    before = current_version(client, runtime_id, endpoint_name)
    if before is None:
        raise SystemExit(f"'{endpoint_name}' 엔드포인트를 찾을 수 없습니다.")

    target_version = version or previous_version(client, runtime_id, before)

    if before == target_version:
        print(f"{endpoint_name}은(는) 이미 V{target_version}을 가리키고 있습니다.")
        return

    response = client.update_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        endpointName=endpoint_name,
        agentRuntimeVersion=target_version,
        description=f"V{target_version}으로 롤백",
    )

    print(f"롤백 요청: {endpoint_name} V{before} -> V{target_version}")
    print(f"  상태: {response.get('status')}")
    print("\n확인: uv run python ops/01_list_versions.py")


if __name__ == "__main__":
    main()
