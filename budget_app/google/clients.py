from googleapiclient.discovery import build
from budget_app.google.auth import get_oauth_credentials


def get_drive_service():
    return build("drive", "v3", credentials=get_oauth_credentials())


def get_sheets_service():
    return build("sheets", "v4", credentials=get_oauth_credentials())