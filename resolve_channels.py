import os
from pathlib import Path

import yaml
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.yaml"))
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def youtube_client():
    required = [
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN",
    ]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing environment variables: " + ", ".join(missing))

    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def main():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    channels = [str(x).strip() for x in cfg.get("channels", []) if str(x).strip()]
    handles = [value for value in channels if value.startswith("@")]
    if not handles:
        return

    youtube = youtube_client()
    resolved = {}
    for handle in handles:
        response = youtube.channels().list(
            part="snippet",
            forHandle=handle,
            maxResults=1,
        ).execute()
        items = response.get("items", [])
        if not items:
            raise ValueError(f"Channel handle not found or unavailable: {handle}")
        channel_id = items[0]["id"]
        channel_name = items[0].get("snippet", {}).get("title", handle)
        resolved[handle] = channel_id
        print(f"[HANDLE] {handle} -> {channel_id} ({channel_name})")

    cfg["channels"] = [resolved.get(value, value) for value in channels]

    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    main()
