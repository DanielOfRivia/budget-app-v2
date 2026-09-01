import plaid
import streamlit as st
from cryptography.fernet import Fernet
from plaid.api import plaid_api


def get_plaid_client() -> plaid_api.PlaidApi:
    client_id = st.secrets["plaid"]["client_id"]
    secret = st.secrets["plaid"]["secret"]
    env = st.secrets["plaid"].get("env", "sandbox")

    host = plaid.Environment.Sandbox if env == "sandbox" else plaid.Environment.Production
    configuration = plaid.Configuration(host=host, api_key={"clientId": client_id, "secret": secret})
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


def _fernet() -> Fernet:
    key = st.secrets["plaid"]["token_encryption_key"]
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
