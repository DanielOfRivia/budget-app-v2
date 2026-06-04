import tomllib
from google_auth_oauthlib.flow import InstalledAppFlow

# Define the scope (using the highly secure drive.file scope)
SCOPES = ['https://www.googleapis.com/auth/drive.file','https://www.googleapis.com/auth/spreadsheets']

def load_client_config_from_secrets(path='.streamlit/secrets.toml'):
    with open(path, 'rb') as f:
        secrets = tomllib.load(f)

    if 'google_oauth' not in secrets:
        raise ValueError('Missing [google_oauth] section in secrets.toml')

    client_config = {
        'installed': {
            'client_id': secrets['google_oauth']['client_id'],
            'client_secret': secrets['google_oauth']['client_secret'],
            'auth_uri': secrets['google_oauth'].get('auth_uri', 'https://accounts.google.com/o/oauth2/auth'),
            'token_uri': secrets['google_oauth'].get('token_uri', 'https://oauth2.googleapis.com/token'),
            'redirect_uris': secrets['google_oauth'].get('redirect_uris', ['http://localhost'])
        }
    }
    return client_config


def main():
    client_config = load_client_config_from_secrets()
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    
    # This will launch a local browser window for you to log in
    print("Opening browser window for authentication...")
    creds = flow.run_local_server(port=0)
    
    # Print the missing pieces for your secrets.toml!
    print("\n--- COPY AND PASTE THIS INTO YOUR SECRETS.TOML ---")
    print(f'client_id = "{creds.client_id}"')
    print(f'client_secret = "{creds.client_secret}"')
    print(f'refresh_token = "{creds.refresh_token}"')
    print("--------------------------------------------------")

if __name__ == '__main__':
    main()