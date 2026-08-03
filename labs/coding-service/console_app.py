"""CodingService 팀 콘솔.

실행:
    uv run --with streamlit==1.60.0 streamlit run console_app.py
"""

from __future__ import annotations

import json
import os

import boto3
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError
from runtime_client import CodingRuntimeClient

REGION = os.environ.get("AWS_REGION", "us-west-2")
RUNTIME_ARN = os.environ.get("CODING_RUNTIME_ARN", "")
WORK_LOG_BUCKET = os.environ.get("CODING_WORK_LOG_BUCKET", "")
WORK_LOG_PREFIX = os.environ.get("CODING_WORK_LOG_PREFIX", "coding-service/work-logs/")


@st.cache_resource
def _runtime_client(runtime_arn: str) -> CodingRuntimeClient:
    return CodingRuntimeClient(runtime_arn, region=REGION)


@st.cache_resource
def _s3_client():
    return boto3.client("s3", region_name=REGION)


def load_work_logs(limit: int = 100) -> list[dict]:
    """최근 수정된 S3 Files JSONL 객체부터 제한된 수의 작업 로그를 읽습니다."""
    if not WORK_LOG_BUCKET:
        return []
    client = _s3_client()
    records: list[dict] = []
    try:
        objects: list[dict] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=WORK_LOG_BUCKET, Prefix=WORK_LOG_PREFIX):
            objects.extend(
                item for item in page.get("Contents", []) if item["Key"].endswith(".jsonl")
            )
        objects.sort(key=lambda item: item["LastModified"], reverse=True)
        for item in objects:
            body = client.get_object(Bucket=WORK_LOG_BUCKET, Key=item["Key"])["Body"].read()
            for line in reversed(body.decode("utf-8").splitlines()):
                if line.strip():
                    records.append(json.loads(line))
                if len(records) >= limit:
                    break
            if len(records) >= limit:
                break
    except (BotoCoreError, ClientError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        st.warning(f"작업 이력 조회 실패: {type(exc).__name__}")
        return []
    return sorted(records, key=lambda item: item.get("timestamp", ""), reverse=True)


st.set_page_config(page_title="CodingService 팀 콘솔", page_icon="🛠️", layout="wide")
st.title("🛠️ CodingService 팀 콘솔")
st.caption("AgentCore Runtime 요청·결정적 테스트·S3 Files 감사 이력을 한 화면에서 확인합니다.")

if "coding_session_id" not in st.session_state and RUNTIME_ARN:
    st.session_state.coding_session_id = _runtime_client(RUNTIME_ARN).new_session_id("team-console")

if not RUNTIME_ARN:
    st.warning("CODING_RUNTIME_ARN 환경 변수를 설정해 주세요.")
else:
    client = _runtime_client(RUNTIME_ARN)
    session_id = st.text_input("Runtime sessionId", value=st.session_state.coding_session_id)
    request_text = st.text_area(
        "코딩 요청",
        placeholder="예: repo/reservation.py에 영업시간 검증을 추가하고 pytest를 작성해 주세요.",
        height=120,
    )

    if st.button("에이전트 실행", type="primary", disabled=not request_text.strip()):
        with st.spinner("격리된 Runtime 세션에서 작업 중입니다..."):
            response = client.invoke(prompt=request_text, session_id=session_id)
        if response.get("status") == "COMPLETED":
            st.success("작업 완료")
        else:
            st.error(f"작업 상태: {response.get('status', 'UNKNOWN')}")
        st.code(response.get("result", ""), language="text")

    left, right = st.columns(2)
    with left:
        if st.button("pytest 재검증"):
            with st.spinner("Runtime command 실행 중..."):
                result = client.run_command(
                    command=(
                        "mkdir -p /mnt/workspace/.runtime-home && cd /mnt/workspace && "
                        "env -i AWS_EC2_METADATA_DISABLED=true "
                        "HOME=/mnt/workspace/.runtime-home LANG=C.UTF-8 "
                        "PATH=/app/.venv/bin:/usr/local/bin:/usr/bin:/bin "
                        "PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 python -m pytest -q"
                    ),
                    session_id=session_id,
                    timeout=300,
                )
            st.code(result.stdout + result.stderr, language="text")
            st.write(f"exit={result.exit_code}, status={result.status}")
    with right:
        if st.button("Git diff 조회"):
            with st.spinner("Runtime command 실행 중..."):
                result = client.run_command(
                    command="git -C /mnt/workspace/repo diff --stat",
                    session_id=session_id,
                    timeout=60,
                )
            st.code(result.stdout + result.stderr, language="text")
            st.write(f"exit={result.exit_code}, status={result.status}")

st.divider()
st.subheader("공유 작업 이력")
if not WORK_LOG_BUCKET:
    st.info("S3 Files 연결 후 CODING_WORK_LOG_BUCKET을 설정하면 팀 이력이 표시됩니다.")
else:
    logs = load_work_logs()
    if logs:
        st.dataframe(
            [
                {
                    "시간": item.get("timestamp"),
                    "세션": item.get("sessionId"),
                    "요청": item.get("promptPreview")
                    or f"sha256:{item.get('promptSha256', '')[:12]}",
                    "상태": item.get("status"),
                    "결과": item.get("resultPreview")
                    or f"sha256:{item.get('resultSha256', '')[:12]}",
                }
                for item in logs
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("아직 조회할 공유 작업 이력이 없습니다.")
