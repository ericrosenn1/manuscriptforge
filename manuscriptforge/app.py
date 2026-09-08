from __future__ import annotations

import sys
from pathlib import Path

from manuscriptforge.config import validate_project
from manuscriptforge.pipeline.workflow import (
    run_audit,
    run_build_claims,
    run_draft,
    run_ingest,
    run_profile_style,
)
from manuscriptforge.utils.io import latest_run_dir, read_json


def main() -> None:
    try:
        import streamlit as st
    except Exception as exc:  # pragma: no cover - optional UI
        raise RuntimeError("Streamlit is required for the UI") from exc

    project_dir = Path(sys.argv[-1]) if len(sys.argv) > 1 else Path.cwd()
    st.set_page_config(page_title="ManuscriptForge", layout="wide")
    st.title("ManuscriptForge")
    st.caption(str(project_dir))

    col1, col2, col3, col4, col5 = st.columns(5)
    if col1.button("Validate"):
        result = validate_project(project_dir)
        st.session_state["validation"] = result
    if col2.button("Ingest"):
        st.session_state["run_dir"] = run_ingest(project_dir)
    if col3.button("Profile"):
        st.session_state["run_dir"] = run_profile_style(project_dir)
    if col4.button("Claims"):
        st.session_state["run_dir"] = run_build_claims(project_dir)
    if col5.button("Draft"):
        st.session_state["run_dir"] = run_draft(project_dir)

    if st.button("Audit Latest"):
        st.session_state["run_dir"] = run_audit(project_dir)

    result = st.session_state.get("validation")
    if result:
        st.subheader("Validation")
        st.write({"errors": result.errors, "warnings": result.warnings, "infos": result.infos})

    run_dir = latest_run_dir(project_dir)
    if run_dir:
        st.subheader("Latest Run")
        st.code(str(run_dir))
        tabs = st.tabs(["Manifest", "Style Profile", "Claim Registry", "Manuscript", "Reports"])
        manifest_path = run_dir / "run_manifest.json"
        if manifest_path.exists():
            tabs[0].json(read_json(manifest_path))
        style_path = run_dir / "style_profile.json"
        if style_path.exists():
            tabs[1].json(read_json(style_path))
        claims_path = run_dir / "claim_registry.json"
        if claims_path.exists():
            tabs[2].json(read_json(claims_path))
        manuscript_path = run_dir / "manuscript.md"
        if manuscript_path.exists():
            tabs[3].markdown(manuscript_path.read_text(encoding="utf-8"))
        report_path = run_dir / "reviewer_critique.md"
        if report_path.exists():
            tabs[4].markdown(report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
