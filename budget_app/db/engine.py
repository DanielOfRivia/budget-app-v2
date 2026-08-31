import streamlit as st


def get_connection():
    """Cached SQLAlchemy-backed connection to Postgres, configured via
    st.secrets["connections"]["postgres"]["url"].

    pool_pre_ping is required here: the underlying engine is cached across
    Streamlit reruns, and Neon suspends/drops idle connections, so a pooled
    connection can go stale between page visits. Pre-ping tests it and
    transparently reconnects instead of raising on the next query."""
    return st.connection("postgres", type="sql", pool_pre_ping=True)
