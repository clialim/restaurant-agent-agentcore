"""dining-coder 미니 콘솔 — Coder·Reviewer·Tester 자기 교정 루프를 브라우저에서 확인합니다.

코딩 요청을 입력하면 코디네이터가 작성→검토→테스트 루프를 최대 3회 돌리고,
라운드별 판정(Reviewer/Tester)과 최종 코드를 화면에 보여줍니다.

실행 (streamlit을 프로젝트 락에 추가하지 않고 임시로 설치해 실행):
    uv run --with streamlit streamlit run app.py

이 명령은 장시간 실행되는 서버이므로 터미널에서 직접 실행하세요.
"""

from __future__ import annotations

import streamlit as st
from orchestrator import build_with_review
from tools import WORKSPACE

TARGET = "reservation.py"

st.set_page_config(page_title="dining-coder 리뷰 루프")
st.title("dining-coder — 리뷰·테스트 자기 교정 루프")
st.caption("요청을 입력하면 Coder·Reviewer·Tester가 최대 3회 루프로 코드를 다듬습니다.")

if prompt := st.chat_input("코딩 요청을 입력하세요"):
    with st.chat_message("user"):
        st.write(prompt)

    with st.chat_message("assistant"):
        with st.spinner("에이전트 루프 실행 중..."):
            result = build_with_review(prompt, target=TARGET)

        if result["state"] == "APPROVED":
            st.success(f"통과 (라운드 {result['rounds']})")
        else:
            st.warning(f"최선 결과 (라운드 {result['rounds']} — 상한 초과)")
            if result.get("remaining"):
                with st.expander("남은 지적"):
                    st.text(result["remaining"])

        for entry in result["history"]:
            quality_approved = entry.get("quality_approved", entry["approved"])
            security_approved = entry.get("security_approved", entry["approved"])
            quality_verdict = "APPROVED" if quality_approved else "NEEDS_CHANGES"
            security_verdict = "APPROVED" if security_approved else "NEEDS_CHANGES"
            status = "통과" if entry["approved"] and entry["passed"] else "재작업"
            st.write(
                f"[{status}] 라운드 {entry['round']} — "
                f"품질: {quality_verdict} · 보안: {security_verdict}"
                f" · Tester: {entry['test']}"
            )
            if not quality_approved:
                with st.expander(f"라운드 {entry['round']} 품질 리뷰 지적"):
                    st.text(entry.get("quality_review", ""))
            if not security_approved:
                with st.expander(f"라운드 {entry['round']} 보안 리뷰 지적"):
                    st.text(entry.get("security_review", ""))

        st.subheader("최종 코드")
        target_file = WORKSPACE / TARGET
        if target_file.exists():
            st.code(target_file.read_text(encoding="utf-8"), language="python")
        else:
            st.error(f"workspace/{TARGET}를 찾을 수 없습니다.")
