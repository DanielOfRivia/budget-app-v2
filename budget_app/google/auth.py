import json
import base64
import streamlit as st
from google.oauth2.credentials import Credentials
from requests_oauthlib import OAuth2Session


def get_oauth_credentials():
    token_info = st.session_state.oauth_token
    return Credentials(
        token=token_info['access_token'],
        refresh_token=token_info.get('refresh_token'),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=st.secrets["google_oauth"]["client_id"],
        client_secret=st.secrets["google_oauth"]["client_secret"]
    )


def login_to_google():
    CLIENT_ID = st.secrets["google_oauth"]["client_id"]
    CLIENT_SECRET = st.secrets["google_oauth"]["client_secret"]
    SCOPES = ['openid',
            'https://www.googleapis.com/auth/drive.file',
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/userinfo.profile', # To get user's name
            'https://www.googleapis.com/auth/userinfo.email'] # To get user's email
    
    REDIRECT_URI = "https://danylo-budget-app.streamlit.app/"  if st.config.get_option("server.headless") else "http://localhost:8501/"

    google = OAuth2Session(CLIENT_ID, scope=SCOPES, redirect_uri=REDIRECT_URI)
    
    # Check if returning from Google with an auth code in the URL parameters
    query_params = st.query_params
    if "code" in query_params:
        try:
            # Exchange authorization code for access tokens
            token = google.fetch_token(
                'https://oauth2.googleapis.com/token',
                client_secret=CLIENT_SECRET,
                code=query_params["code"]
            )
            st.session_state.oauth_token = token
            # 2. Extract and decode the id_token
            if 'id_token' in token:
                # JWTs are split by dots; the middle part is the data payload
                payload = token['id_token'].split('.')[1]
                # Add base64 padding to avoid decoding errors
                payload += '=' * (-len(payload) % 4)
                
                # Decode the JSON
                user_info = json.loads(base64.b64decode(payload).decode('utf-8'))
                
                # Instantly save to session state!
                st.session_state.user_email = user_info.get("email", "Unknown Email")
                st.session_state.user_name = user_info.get("name", "")
            # Clear URL parameters to clean up the workspace
            st.query_params.clear()
            st.rerun()
        except Exception as e:
            st.error(f"Authentication failed: {e}")

    # If no token exists in session, display the Login UI
    if "oauth_token" not in st.session_state:
        authorization_url, state = google.authorization_url(
            'https://accounts.google.com/o/oauth2/auth',
            access_type="offline",
            prompt="select_account"
        )
        # app.py's sidebar content (account panel, page nav) never runs on
        # this branch — it's all after this function's st.stop() — but
        # Streamlit still reserves the sidebar's collapse toggle regardless
        # of whether anything's written to it. Hide it explicitly rather
        # than leave an empty control visible before sign-in.
        st.html(
            """
            <style>
            [data-testid="stSidebar"],
            [data-testid="stSidebarCollapsedControl"] { display: none; }
            </style>
            """
        )
        st.title("📊 Budget Automation App")
        st.write("Please sign in with your Google Account to process statements and update your budget.")
        
        # Open login window
        st.link_button("🔑 Sign In With Google", authorization_url, use_container_width=True)
        st.stop()


def logout_google():
    """Clear all Google OAuth session state."""
    del st.session_state.oauth_token
    del st.session_state.user_email
    del st.session_state.user_name
    st.rerun()