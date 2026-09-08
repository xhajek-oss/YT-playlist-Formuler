import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/youtube"]
CLIENT_SECRET_FILE = Path("client_secret.json")


def main():
    if not CLIENT_SECRET_FILE.exists():
        raise SystemExit(
            "Missing client_secret.json. Download your OAuth Desktop app "
            "credentials from Google Cloud Console and save them next to this script."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE),
        scopes=SCOPES,
    )

    credentials = flow.run_local_server(
        host="localhost",
        port=0,
        authorization_prompt_message="Open this URL in your browser:\n{url}",
        success_message="Authorization complete. You can close this browser window.",
        open_browser=True,
        access_type="offline",
        prompt="consent",
    )

    client_data = json.loads(CLIENT_SECRET_FILE.read_text(encoding="utf-8"))
    app = client_data.get("installed") or client_data.get("web") or {}

    print("\nAdd these values to GitHub repository Secrets:\n")
    print(f"YOUTUBE_CLIENT_ID={app.get('client_id', '')}")
    print(f"YOUTUBE_CLIENT_SECRET={app.get('client_secret', '')}")
    print(f"YOUTUBE_REFRESH_TOKEN={credentials.refresh_token}")

    if not credentials.refresh_token:
        print(
            "\nWARNING: Google did not return a refresh token. "
            "Revoke the app access in your Google Account and run this helper again."
        )


if __name__ == "__main__":
    main()
