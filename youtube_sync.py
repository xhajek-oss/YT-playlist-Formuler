import json
import os
from datetime import datetime, timezone
from pathlib import Path

import isodate
import yaml
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


CONFIG_PATH = Path(os.getenv("CONFIG_PATH", "config.yaml"))
STATE_PATH = Path(os.getenv("STATE_PATH", "state.json"))
SCOPES = ["https://www.googleapis.com/auth/youtube"]


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    playlist_id = os.getenv("YOUTUBE_PLAYLIST_ID", "").strip()
    channels = [str(x).strip() for x in cfg.get("channels", []) if str(x).strip()]
    source_playlists = []
    for item in cfg.get("source_playlists", []) or []:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("playlist_id", "")).strip()
        if source_id:
            source_playlists.append({
                "playlist_id": source_id,
                "name": str(item.get("name") or source_id).strip(),
            })

    if not playlist_id:
        raise ValueError("Missing YOUTUBE_PLAYLIST_ID environment variable / GitHub Secret.")
    if not channels and not source_playlists:
        raise ValueError("Configure at least one channel or source playlist in config.yaml.")

    return {
        "playlist_id": playlist_id,
        "channels": channels,
        "source_playlists": source_playlists,
        "min_duration_seconds": float(cfg.get("min_duration_minutes", 5)) * 60,
        "latest_count": max(1, min(int(cfg.get("latest_videos_per_channel", 15)), 50)),
    }


def load_state():
    if not STATE_PATH.exists():
        return {"channels": {}, "source_playlists": {}}
    with STATE_PATH.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("channels", {})
    state.setdefault("source_playlists", {})
    return state


def save_state(state):
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def youtube_client():
    required = ["YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET", "YOUTUBE_REFRESH_TOKEN"]
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


def get_channel_uploads_playlist(youtube, channel_id):
    response = youtube.channels().list(
        part="snippet,contentDetails", id=channel_id, maxResults=1
    ).execute()
    items = response.get("items", [])
    if not items:
        raise ValueError(f"Channel not found or unavailable: {channel_id}")
    item = items[0]
    return item["snippet"]["title"], item["contentDetails"]["relatedPlaylists"]["uploads"]


def get_latest_uploads(youtube, uploads_playlist_id, max_results):
    response = youtube.playlistItems().list(
        part="snippet,contentDetails", playlistId=uploads_playlist_id, maxResults=max_results
    ).execute()
    result = []
    for item in response.get("items", []):
        video_id = item.get("contentDetails", {}).get("videoId")
        snippet = item.get("snippet", {})
        if video_id:
            result.append({
                "video_id": video_id,
                "title": snippet.get("title", video_id),
                "published_at": snippet.get("publishedAt", ""),
            })
    return result


def get_all_source_playlist_video_ids(youtube, playlist_id):
    result = []
    page_token = None
    while True:
        response = youtube.playlistItems().list(
            part="contentDetails", playlistId=playlist_id, maxResults=50, pageToken=page_token
        ).execute()
        for item in response.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId")
            if video_id:
                result.append(video_id)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return result


def add_video_details(youtube, videos):
    if not videos:
        return videos
    by_id = {v["video_id"]: v for v in videos}
    ids = list(by_id)
    for start in range(0, len(ids), 50):
        response = youtube.videos().list(
            part="snippet,contentDetails,status",
            id=",".join(ids[start:start + 50]),
            maxResults=50,
        ).execute()
        for item in response.get("items", []):
            video = by_id.get(item["id"])
            if video is None:
                continue
            snippet = item.get("snippet", {})
            video["title"] = snippet.get("title", video["video_id"])
            video["published_at"] = snippet.get("publishedAt", "")
            duration = item.get("contentDetails", {}).get("duration")
            if duration:
                video["duration_seconds"] = int(isodate.parse_duration(duration).total_seconds())
            video["privacy_status"] = item.get("status", {}).get("privacyStatus")
    return videos


def get_playlist_video_ids(youtube, playlist_id):
    return set(get_all_source_playlist_video_ids(youtube, playlist_id))


def add_video_to_playlist(youtube, playlist_id, video_id):
    youtube.playlistItems().insert(
        part="snippet",
        body={"snippet": {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }},
    ).execute()


def parse_dt(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def main():
    cfg = load_config()
    state = load_state()
    youtube = youtube_client()
    playlist_video_ids = get_playlist_video_ids(youtube, cfg["playlist_id"])
    candidates = []
    state_changed = False

    configured_channels = set(cfg["channels"])
    for old_id in list(state["channels"]):
        if old_id not in configured_channels:
            del state["channels"][old_id]
            state_changed = True

    configured_sources = {x["playlist_id"] for x in cfg["source_playlists"]}
    for old_id in list(state["source_playlists"]):
        if old_id not in configured_sources:
            del state["source_playlists"][old_id]
            state_changed = True

    # Regular channels keep the existing behavior: new uploads only, >= configured duration.
    for channel_id in cfg["channels"]:
        channel_name, uploads_playlist_id = get_channel_uploads_playlist(youtube, channel_id)
        uploads = get_latest_uploads(youtube, uploads_playlist_id, cfg["latest_count"])
        if not uploads:
            print(f"[{channel_name}] No uploads found.")
            continue

        newest_seen = uploads[0]["published_at"]
        if channel_id not in state["channels"]:
            state["channels"][channel_id] = {
                "channel_name": channel_name,
                "last_seen_published_at": newest_seen,
            }
            state_changed = True
            print(f"[{channel_name}] Initialized. Existing videos were not imported.")
            continue

        last_seen = parse_dt(state["channels"][channel_id].get("last_seen_published_at"))
        fresh = [v for v in uploads if parse_dt(v["published_at"]) > last_seen]
        add_video_details(youtube, fresh)

        for video in fresh:
            duration = video.get("duration_seconds")
            if duration is None:
                print(f"[{channel_name}] Skip (duration unavailable): {video['title']}")
                continue
            if duration < cfg["min_duration_seconds"]:
                print(f"[{channel_name}] Skip < {cfg['min_duration_seconds']/60:g} min: {video['title']} ({duration}s)")
                continue
            if video.get("privacy_status") != "public":
                print(f"[{channel_name}] Skip non-public: {video['title']}")
                continue
            if video["video_id"] in playlist_video_ids:
                print(f"[{channel_name}] Already in playlist: {video['title']}")
                continue
            candidates.append({**video, "source_name": channel_name, "source_playlist_id": None})

        if parse_dt(newest_seen) > last_seen:
            state["channels"][channel_id] = {
                "channel_name": channel_name,
                "last_seen_published_at": newest_seen,
            }
            state_changed = True

    # Curated source playlists: every newly appearing public video is eligible,
    # with no duration or static-image filter.
    for source in cfg["source_playlists"]:
        source_id = source["playlist_id"]
        source_name = source["name"]
        current_ids = get_all_source_playlist_video_ids(youtube, source_id)

        if source_id not in state["source_playlists"]:
            state["source_playlists"][source_id] = {
                "name": source_name,
                "seen_video_ids": current_ids,
            }
            state_changed = True
            print(f"[{source_name}] Source playlist initialized with {len(current_ids)} existing video(s); no backfill.")
            continue

        seen = set(state["source_playlists"][source_id].get("seen_video_ids", []))
        new_ids = [video_id for video_id in current_ids if video_id not in seen]
        new_videos = add_video_details(youtube, [{"video_id": x} for x in new_ids])

        for video in new_videos:
            if video.get("privacy_status") != "public":
                print(f"[{source_name}] Retry later (non-public/unavailable): {video.get('title', video['video_id'])}")
                continue
            if video["video_id"] in playlist_video_ids:
                print(f"[{source_name}] Already in destination playlist: {video['title']}")
                seen.add(video["video_id"])
                state_changed = True
                continue
            candidates.append({**video, "source_name": source_name, "source_playlist_id": source_id})

        state["source_playlists"][source_id]["seen_video_ids"] = sorted(seen)

    candidates.sort(key=lambda x: parse_dt(x.get("published_at")))

    added = 0
    for video in candidates:
        try:
            add_video_to_playlist(youtube, cfg["playlist_id"], video["video_id"])
            playlist_video_ids.add(video["video_id"])
            added += 1
            mins = video.get("duration_seconds", 0) / 60
            print(f"[ADD] {video['source_name']}: {video['title']} ({mins:.1f} min)")
            source_id = video.get("source_playlist_id")
            if source_id:
                seen = set(state["source_playlists"][source_id].get("seen_video_ids", []))
                seen.add(video["video_id"])
                state["source_playlists"][source_id]["seen_video_ids"] = sorted(seen)
                state_changed = True
        except HttpError as exc:
            print(f"[ERROR] Could not add {video['video_id']}: {exc}")
            raise

    if state_changed:
        save_state(state)
    print(f"Done. Added {added} new video(s).")


if __name__ == "__main__":
    main()
