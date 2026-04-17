import time

import streamlit as st
from api_client import APIClient
from components import (
    inject_custom_css,
    render_header,
    render_report,
    render_status_tracker,
)

# --- Page Config ---
st.set_page_config(
    page_title="Colt-AI | Research Intelligence",
    page_icon="🤖",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# --- Initialize Session State ---
if "request_id" not in st.session_state:
    st.session_state.request_id = None
if "company_name" not in st.session_state:
    st.session_state.company_name = None
if "status" not in st.session_state:
    st.session_state.status = None
if "result_data" not in st.session_state:
    st.session_state.result_data = None
if "error" not in st.session_state:
    st.session_state.error = None

# --- Main Logic ---


def poll_status():
    """Polls the API for status updates until completion."""
    req_id = st.session_state.request_id
    if not req_id:
        return

    # Create an empty placeholder to update the status tracker in place
    status_placeholder = st.empty()

    while st.session_state.status not in ["COMPLETED", "FAILED"]:
        status_res = APIClient.check_status(req_id)

        if status_res.get("error"):
            st.session_state.error = status_res.get("detail", "Error fetching status.")
            st.session_state.status = "FAILED"
            break

        current_status = status_res.get("status", "PENDING")
        st.session_state.status = current_status

        with status_placeholder.container():
            render_status_tracker(current_status, st.session_state.company_name)

        if current_status in ["COMPLETED", "FAILED"]:
            break

        # Poll every 5 seconds
        time.sleep(5)

    # Fetch results when completed
    if st.session_state.status == "COMPLETED":
        # Clear the status tracker once done, or leave it showing "Completed"
        status_placeholder.empty()
        with st.spinner("Fetching final report..."):
            result_res = APIClient.get_result(req_id, include_content=True)
            if result_res.get("error"):
                st.session_state.error = result_res.get(
                    "detail", "Error fetching results."
                )
            else:
                st.session_state.result_data = result_res
        st.rerun()


def main():
    inject_custom_css()
    render_header()

    # --- Input Form ---
    # Only show input if we are not currently processing
    if st.session_state.status not in ["PENDING", "PROCESSING", "COMPLETED"]:
        with st.form("research_form", clear_on_submit=False):
            col1, col2 = st.columns([4, 1])
            with col1:
                company = st.text_input(
                    "Company Name",
                    placeholder="e.g. Acme Corp, OpenAI...",
                    label_visibility="collapsed",
                )
            with col2:
                submitted = st.form_submit_button("Research")

            if submitted and company.strip():
                # Reset state
                st.session_state.error = None
                st.session_state.result_data = None

                # Submit API Request
                with st.spinner("Submitting request..."):
                    res = APIClient.submit_research(company.strip())

                if res.get("error"):
                    st.error(f"Failed to submit: {res.get('detail')}")
                else:
                    st.session_state.request_id = res.get("request_id")
                    st.session_state.company_name = company.strip()
                    st.session_state.status = res.get("status", "PENDING")
                    st.rerun()
            elif submitted and not company.strip():
                st.warning("Please enter a company name.")

    # --- Error Handling ---
    if st.session_state.error:
        st.error(st.session_state.error)
        if st.button("Start New Research"):
            # Reset state
            for key in ["request_id", "company_name", "status", "result_data", "error"]:
                st.session_state[key] = None
            st.rerun()

    # --- Progress & Results ---
    if st.session_state.status in ["PENDING", "PROCESSING"]:
        # We use a button to cancel/reset since we get stuck in the loop otherwise
        if st.button("Cancel & Go Back"):
            for key in ["request_id", "company_name", "status", "result_data", "error"]:
                st.session_state[key] = None
            st.rerun()

        poll_status()

    elif st.session_state.status == "COMPLETED" and st.session_state.result_data:
        render_report(st.session_state.result_data)

        st.markdown("---")
        if st.button("Start New Research", key="new_research_bottom"):
            for key in ["request_id", "company_name", "status", "result_data", "error"]:
                st.session_state[key] = None
            st.rerun()


if __name__ == "__main__":
    main()
