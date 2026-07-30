"""버전과 엔드포인트를 조회합니다.

사용법:
    uv run python ops/01_list_versions.py
"""

from _common import RUNTIME_NAME, control_client, get_runtime_id, use_utf8_stdout


def main() -> None:
    use_utf8_stdout()
    runtime_id = get_runtime_id()
    client = control_client()

    print(f"런타임: {RUNTIME_NAME} ({runtime_id})\n")

    versions = client.list_agent_runtime_versions(agentRuntimeId=runtime_id)
    print("=== 버전 목록 ===")
    for item in versions.get("agentRuntimes", []):
        print(
            f"  V{item.get('agentRuntimeVersion')} | "
            f"상태: {item.get('status')} | "
            f"수정: {item.get('lastUpdatedAt')}"
        )

    endpoints = client.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
    print("\n=== 엔드포인트 목록 ===")
    for item in endpoints.get("runtimeEndpoints", []):
        # DEFAULT는 최신 버전을 자동 추적하고, 사용자 생성 엔드포인트는 지정 버전에 고정됩니다.
        name = item.get("name")
        kind = "자동 추적" if name == "DEFAULT" else "버전 고정"
        print(
            f"  {name} | "
            f"실행 버전: V{item.get('liveVersion')} | "
            f"{kind} | "
            f"상태: {item.get('status')}"
        )
        if item.get("description"):
            print(f"      설명: {item['description']}")


if __name__ == "__main__":
    main()
