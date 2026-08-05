"""배포된 CodingService를 호출하고 같은 세션에서 pytest를 실행하는 smoke test."""

from __future__ import annotations

import argparse
import os

from runtime_client import CodingRuntimeClient, print_stream

DEFAULT_PROMPT = (
    "workspace에 capacity.py와 test_capacity.py를 작성해 주세요. "
    "예약 인원과 수용 인원을 정수로 검증하고 초과 여부를 반환한 뒤 pytest로 검증해 주세요."
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-arn", default=os.environ.get("CODING_RUNTIME_ARN"))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--session-id")
    args = parser.parse_args()
    if not args.runtime_arn:
        parser.error("--runtime-arn 또는 CODING_RUNTIME_ARN이 필요합니다.")

    client = CodingRuntimeClient(args.runtime_arn)
    session_id = args.session_id or client.new_session_id("coding-smoke")
    response = client.invoke(prompt=args.prompt, session_id=session_id)
    print(f"sessionId: {session_id}")
    print(f"agent status: {response.get('status')}")
    print(response.get("result", ""))

    command_result = client.run_command(
        command=(
            "mkdir -p /mnt/workspace/.runtime-home && cd /mnt/workspace && "
            "env -i AWS_EC2_METADATA_DISABLED=true "
            "HOME=/mnt/workspace/.runtime-home LANG=C.UTF-8 "
            "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin "
            "PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 python -m pytest -q"
        ),
        session_id=session_id,
        timeout=300,
        on_output=print_stream,
    )
    print(f"\ncommand status: {command_result.status}, exit: {command_result.exit_code}")
    return 0 if response.get("status") == "COMPLETED" and command_result.exit_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
