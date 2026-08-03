"""CodingService Runtime 호출과 command 이벤트 스트림 처리를 공통화합니다."""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
QUALIFIER = "DEFAULT"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{33,100}$")
RUNTIME_ARN_PATTERN = re.compile(
    r"^arn:aws(?:-[a-z]+)?:bedrock-agentcore:[a-z0-9-]+:\d{12}:runtime/[A-Za-z0-9_-]+$"
)
MAX_COMMAND_BYTES = 64 * 1024
COMMAND_ERROR_EVENTS = (
    "accessDeniedException",
    "internalServerException",
    "resourceNotFoundException",
    "runtimeClientError",
    "serviceQuotaExceededException",
    "throttlingException",
    "validationException",
)


@dataclass(frozen=True)
class CommandResult:
    """Runtime command의 출력과 종료 판정."""

    stdout: str
    stderr: str
    exit_code: int | None
    status: str | None


class CodingRuntimeClient:
    """동일 runtimeSessionId로 Agent 호출과 결정적 명령을 연결합니다."""

    def __init__(self, runtime_arn: str, region: str = REGION) -> None:
        if not RUNTIME_ARN_PATTERN.fullmatch(runtime_arn):
            raise ValueError("올바른 AgentCore Runtime ARN이 필요합니다.")
        self.runtime_arn = runtime_arn
        self.client = boto3.client("bedrock-agentcore", region_name=region)

    @staticmethod
    def new_session_id(prefix: str = "coding-service") -> str:
        """AgentCore의 33자 최소 길이를 만족하는 세션 ID를 생성합니다."""
        session_id = f"{prefix}-{uuid.uuid4().hex}"
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("생성한 sessionId 형식이 올바르지 않습니다.")
        return session_id

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            raise ValueError("sessionId는 33~100자의 영문·숫자·하이픈·언더스코어여야 합니다.")

    def invoke(self, *, prompt: str, session_id: str, request_id: str | None = None) -> dict:
        """코딩 요청을 호출하고 JSON 응답을 반환합니다."""
        self._validate_session_id(session_id)
        request_id = request_id or uuid.uuid4().hex
        payload = {
            "prompt": prompt,
            "sessionId": session_id,
            "requestId": request_id,
        }
        response = self.client.invoke_agent_runtime(
            agentRuntimeArn=self.runtime_arn,
            qualifier=QUALIFIER,
            runtimeSessionId=session_id,
            payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        )
        raw = response["response"].read().decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("Runtime 응답이 JSON 객체가 아닙니다.")
        return parsed

    def run_command(
        self,
        *,
        command: str,
        session_id: str,
        timeout: int = 300,
        on_output: Callable[[str, bool], None] | None = None,
    ) -> CommandResult:
        """활성 세션에서 단일 명령을 실행하고 이벤트 스트림을 수집합니다."""
        self._validate_session_id(session_id)
        if not 1 <= len(command.encode("utf-8")) <= MAX_COMMAND_BYTES:
            raise ValueError("command는 1바이트 이상 64KB 이하여야 합니다.")
        if not 1 <= timeout <= 3600:
            raise ValueError("timeout은 1~3600초여야 합니다.")

        response = self.client.invoke_agent_runtime_command(
            agentRuntimeArn=self.runtime_arn,
            qualifier=QUALIFIER,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/vnd.amazon.eventstream",
            body={"command": command, "timeout": timeout},
        )
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        exit_code: int | None = None
        status: str | None = None

        stop_received = False
        for event in response.get("stream", []):
            for error_name in COMMAND_ERROR_EVENTS:
                if error := event.get(error_name):
                    message = error.get("message", "상세 메시지 없음")
                    raise RuntimeError(f"Runtime command {error_name}: {message}")

            chunk = event.get("chunk", {})
            delta = chunk.get("contentDelta", {})
            if output := delta.get("stdout"):
                stdout_parts.append(output)
                if on_output:
                    on_output(output, False)
            if output := delta.get("stderr"):
                stderr_parts.append(output)
                if on_output:
                    on_output(output, True)
            if stop := chunk.get("contentStop"):
                if stop_received:
                    raise RuntimeError("Runtime command가 contentStop을 두 번 반환했습니다.")
                stop_received = True
                exit_code = stop.get("exitCode")
                status = stop.get("status")

        if not stop_received or exit_code is None or status is None:
            raise RuntimeError("Runtime command가 유효한 contentStop 없이 종료되었습니다.")
        return CommandResult(
            stdout="".join(stdout_parts),
            stderr="".join(stderr_parts),
            exit_code=exit_code,
            status=status,
        )


def print_stream(text: str, is_stderr: bool) -> None:
    """CLI에서 command 스트림을 즉시 출력합니다."""
    print(text, end="", file=sys.stderr if is_stderr else sys.stdout, flush=True)
