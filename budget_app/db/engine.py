import streamlit as st


def get_connection():
    """Cached SQLAlchemy-backed connection to Postgres, configured via
    st.secrets["connections"]["postgres"]["url"]."""
    return st.connection("postgres", type="sql")
