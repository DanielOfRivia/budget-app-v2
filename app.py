import streamlit as st
from budget_app.google.auth import login_to_google, logout_google

st.set_page_config(page_title="Budget App", layout="wide")

login_to_google()  # Call the login function to handle OAuth flow

st.sidebar.markdown("### Account")
if st.session_state.get("user_name"):
    st.sidebar.success(f"✅ Welcome, {st.session_state.user_name}!")
    st.sidebar.caption(f"Signed in as: **{st.session_state.user_email}**")
else:
    st.sidebar.success(f"✅ Signed in as: **{st.session_state.user_email}**")
if st.sidebar.button("Log Out"):
    logout_google()

pages = {
    "Overview": [
        st.Page("pages/dashboard.py", title="Dashboard", icon="📊", default=True),
    ],
    "Manage": [
        st.Page("pages/upload.py", title="Upload & Categorize", icon="📥"),
        st.Page("pages/lending.py", title="Lending", icon="🤝"),
    ],
}

st.navigation(pages).run()
