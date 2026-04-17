import time

import streamlit as st


def inject_custom_css():
    """Injects custom CSS to style the Streamlit app."""
    try:
        with open("ui/styles.css") as f:
            css = f.read()
            st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
    except FileNotFoundError:
        pass  # Ignore if css file is missing during development


def render_header():
    """Renders the top level branding and description."""
    st.markdown(
        '<h1 style="text-align: center;">Colt-AI Research</h1>', unsafe_allow_html=True
    )
    st.markdown(
        '<p class="subtitle" style="text-align: center;">Intelligent sales alignment and deep company research powered by AI.</p>',
        unsafe_allow_html=True,
    )


def render_status_tracker(status_str: str, company_name: str):
    """Renders a visual tracker for the research process."""
    st.markdown(f"### Researching: **{company_name}**")

    # Define steps
    steps = [
        {"id": "PENDING", "label": "Request Accepted & Queued", "icon": "1"},
        {"id": "PROCESSING", "label": "Agents Gathering Intelligence", "icon": "2"},
        {"id": "COMPLETED", "label": "Research Compiled & Finished", "icon": "3"},
    ]

    # Simple state machine for visual progress
    current_index = 0
    if status_str == "PROCESSING":
        current_index = 1
    elif status_str == "COMPLETED":
        current_index = 2
    elif status_str == "FAILED":
        st.error("Research failed.")
        return

    st.markdown('<div class="status-container">', unsafe_allow_html=True)
    for i, step in enumerate(steps):
        if i < current_index:
            state_class = "step-completed"
            icon = "✓"
        elif i == current_index:
            state_class = "step-active"
            icon = step["icon"]
            if i == 1:  # If processing, add a spinner equivalent or visual cue
                pass  # CSS handles active state
        else:
            state_class = "step-pending"
            icon = step["icon"]

        st.markdown(
            f"""
            <div class="progress-step">
                <div class="step-icon {state_class}">{icon}</div>
                <div class="step-text">{step["label"]}</div>
            </div>
        """,
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

    if status_str == "PROCESSING":
        with st.spinner(
            "Compiling insights... This usually takes 1-3 minutes depending on the company size."
        ):
            time.sleep(1)  # Visual padding


def render_report(result_data: dict):
    """Renders the final markdown report and actions."""
    company_name = result_data.get("company_name", "Company")
    content = result_data.get("report_content", "*No content available.*")
    download_url = result_data.get("download_url")
    error_msg = result_data.get("error_message")

    if error_msg:
        st.markdown(
            f'<div class="error-alert"><strong>Error processing {company_name}:</strong> {error_msg}</div>',
            unsafe_allow_html=True,
        )
        return

    st.success(f"Successfully generated research for {company_name}!")

    # Action buttons (Download)
    if download_url:
        st.markdown(
            f'''
            <a href="{download_url}" target="_blank" class="download-btn">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right: 8px;">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="7 10 12 15 17 10"></polyline>
                    <line x1="12" y1="15" x2="12" y2="3"></line>
                </svg>
                Download PDF Report
            </a>
        ''',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="report-container">', unsafe_allow_html=True)
    # Streamlit natively handles markdown incredibly well
    st.markdown(content, unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
