"""버전을 고정한 production 엔드포인트를 생성합니다.

사용법:
    uv run python ops/02_create_endpoint.py            # 최신 버전으로 생성
    uv run python ops/02_create_endpoint.py 2          # V2로 고정해 생성
    uv run python ops/02_create_endpoint.py 2 canary   # 이름 지정
"""

import sys

from _common import control_client, get_runtime_id, use_utf8_stdout

DEFAULT_ENDPOINT_NAME = "production"


def latest_version(client, runtime_id: str) -> str:
    """가장 높은 버전 번호를 반환합니다."""
    response = client.list_agent_runtime_versions(agentRuntimeId=runtime_id)
    versions = [
        item["agentRuntimeVersion"]
        for item in response.get("agentRuntimes", [])
        if item.get("agentRuntimeVersion")
    ]
    if not versions:
        raise RuntimeError("조회된 버전이 없습니다. 먼저 배포를 완료하세요.")
    return max(versions, key=int)


def main() -> None:
    use_utf8_stdout()
    version = sys.argv[1] if len(sys.argv) > 1 else None
    endpoint_name = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ENDPOINT_NAME

    runtime_id = get_runtime_id()
    client = control_client()

    target_version = version or latest_version(client, runtime_id)

    response = client.create_agent_runtime_endpoint(
        agentRuntimeId=runtime_id,
        name=endpoint_name,
        agentRuntimeVersion=target_version,
        description=f"V{target_version} 고정 엔드포인트",
    )

    print(f"엔드포인트 생성 요청: {endpoint_name} -> V{target_version}")
    print(f"  상태: {response.get('status')}")
    print(f"  ARN: {response.get('agentRuntimeEndpointArn')}")
    print("\n상태가 READY가 되면 호출할 수 있습니다.")
    print("확인: uv run python ops/01_list_versions.py")


if __name__ == "__main__":
    main()
