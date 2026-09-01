import streamlit as st
from budget_app.google.auth import login_to_google, logout_google

st.set_page_config(page_title="Budget App", layout="wide")

login_to_google()  # Call the login function to handle OAuth flow

# App-level allowlist: anyone who completes Google sign-in but isn't on this
# list is stopped here, before any sidebar/page content (and therefore
# before any Plaid/Gemini call) ever renders. Absent or empty list = no
# restriction, so an app without this secret configured behaves as before.
allowed_emails = st.secrets.get("access", {}).get("allowed_emails")
user_email = st.session_state.get("user_email")
if allowed_emails and user_email not in allowed_emails:
    st.error(f"🚫 This app is restricted. **{user_email}** isn't on the allowed list.")
    if st.button("Sign out and try a different account"):
        logout_google()
    st.stop()

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
        st.Page("pages/transactions.py", title="All Transactions", icon="🧾"),
    ],
}

st.navigation(pages).run()
