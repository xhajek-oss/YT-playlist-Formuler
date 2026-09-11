import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import isodate
import yaml
import yt_dlp
from PIL import Image, ImageChops, ImageStat
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

    min_duration_minutes = float(cfg.get("min_duration_minutes", 5))
    latest_count = int(cfg.get("latest_videos_per_channel", 15))

    static_cfg = cfg.get("static_video_filter", {}) or {}
    static_channels = {
        str(x).strip()
        for x in static_cfg.get("enabled_channels", [])
        if str(x).strip()
    }

    if not playlist_id:
        raise ValueError(
            "Missing YOUTUBE_PLAYLIST_ID environment variable / GitHub Secret."
        )

    if not channels or channels == ["PUT_CHANNEL_ID_HERE"]:
        raise ValueError("Add at least one real YouTube channel ID to config.yaml.")

    return {
        "playlist_id": playlist_id,
        "channels": channels,
        "min_duration_seconds": min_duration_minutes * 60,
        "latest_count": max(1, min(latest_count, 50)),
        "static_video_filter": {
            "enabled_channels": static_channels,
            "sample_count": max(3, min(int(static_cfg.get("sample_count", 10)), 30)),
            "max_mean_difference": float(
                static_cfg.get("max_mean_difference", 2.0)
            ),
            "min_static_fraction": float(
                static_cfg.get("min_static_fraction", 0.90)
            ),
        },
    }


def load_state():
    if not STATE_PATH.exists():
        return {"channels": {}}
    with STATE_PATH.open("r", encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("channels", {})
    return state


def save_state(state):
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


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


def get_channel_uploads_playlist(youtube, channel_id):
    response = youtube.channels().list(
        part="snippet,contentDetails",
        id=channel_id,
        maxResults=1,
    ).execute()

    items = response.get("items", [])
    if not items:
        raise ValueError(f"Channel not found or unavailable: {channel_id}")

    item = items[0]
    return (
        item["snippet"]["title"],
        item["contentDetails"]["relatedPlaylists"]["uploads"],
    )


def get_latest_uploads(youtube, uploads_playlist_id, max_results):
    response = youtube.playlistItems().list(
        part="snippet,contentDetails",
        playlistId=uploads_playlist_id,
        maxResults=max_results,
    ).execute()

    result = []
    for item in response.get("items", []):
        video_id = item.get("contentDetails", {}).get("videoId")
        snippet = item.get("snippet", {})
        if not video_id:
            continue
        result.append({
            "video_id": video_id,
            "title": snippet.get("title", video_id),
            "published_at": snippet.get("publishedAt", ""),
        })
    return result


def add_durations(youtube, videos):
    if not videos:
        return videos

    by_id = {v["video_id"]: v for v in videos}
    ids = list(by_id)

    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        response = youtube.videos().list(
            part="contentDetails,status",
            id=",".join(batch),
            maxResults=50,
        ).execute()

        for item in response.get("items", []):
            video = by_id.get(item["id"])
            if video is None:
                continue

            duration = item.get("contentDetails", {}).get("duration")
            if duration:
                video["duration_seconds"] = int(
                    isodate.parse_duration(duration).total_seconds()
                )
            video["privacy_status"] = item.get("status", {}).get("privacyStatus")

    return videos


def get_playlist_video_ids(youtube, playlist_id):
    result = set()
    page_token = None

    while True:
        response = youtube.playlistItems().list(
            part="contentDetails",
            playlistId=playlist_id,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        for item in response.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId")
            if video_id:
                result.add(video_id)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return result


def add_video_to_playlist(youtube, playlist_id, video_id):
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id,
                },
            }
        },
    ).execute()


def parse_dt(value):
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def mean_frame_difference(path_a, path_b):
    with Image.open(path_a) as image_a, Image.open(path_b) as image_b:
        a = image_a.convert("L")
        b = image_b.convert("L")
        diff = ImageChops.difference(a, b)
        return ImageStat.Stat(diff).mean[0]


def is_static_video(video_id, duration_seconds, filter_cfg):
    sample_count = filter_cfg["sample_count"]
    threshold = filter_cfg["max_mean_difference"]
    min_static_fraction = filter_cfg["min_static_fraction"]

    if duration_seconds <= 0:
        raise RuntimeError(f"Cannot analyze static video with invalid duration: {video_id}")

    with tempfile.TemporaryDirectory(prefix="yt-static-") as tmp:
        tmp_path = Path(tmp)

        ydl_opts = {
            "format": (
                "worst[protocol*=m3u8][height<=360]/"
                "worst[protocol*=m3u8]/"
                "worst[height<=360]/worst"
            ),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["web_safari"],
                }
            },
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"https://www.youtube.com/watch?v={video_id}",
                    download=False,
                )
        except Exception as exc:
            raise RuntimeError(
                f"Static-video stream lookup failed for {video_id}: {exc}"
            ) from exc

        stream_url = info.get("url")
        if not stream_url:
            requested_formats = info.get("requested_formats") or []
            stream_url = next(
                (
                    fmt.get("url")
                    for fmt in requested_formats
                    if fmt.get("vcodec") != "none" and fmt.get("url")
                ),
                None,
            )

        if not stream_url:
            raise RuntimeError(
                f"Static-video stream lookup returned no playable URL for {video_id}"
            )

        http_headers = info.get("http_headers") or {}
        user_agent = http_headers.get("User-Agent")
        referer = http_headers.get("Referer")

        frame_paths = []
        for index in range(sample_count):
            position = (index + 1) / (sample_count + 1)
            timestamp = max(0.0, duration_seconds * position)
            frame_path = tmp_path / f"frame-{index:02d}.png"
            command = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
            ]
            if user_agent:
                command.extend(["-user_agent", user_agent])
            if referer:
                command.extend(["-headers", f"Referer: {referer}\r\n"])
            command.extend([
                "-i",
                stream_url,
                "-frames:v",
                "1",
                "-vf",
                "scale=160:-1:flags=area",
                "-y",
                str(frame_path),
            ])

            try:
                subprocess.run(command, check=True, timeout=60)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                raise RuntimeError(
                    f"ffmpeg stream sample failed for {video_id} at {timestamp:.1f}s"
                ) from exc
            if not frame_path.exists():
                raise RuntimeError(
                    f"ffmpeg produced no sample frame for {video_id}"
                )
            frame_paths.append(frame_path)

        differences = [
            mean_frame_difference(frame_paths[i - 1], frame_paths[i])
            for i in range(1, len(frame_paths))
        ]
        static_pairs = sum(value <= threshold for value in differences)
        static_fraction = static_pairs / len(differences)
        avg_difference = sum(differences) / len(differences)

        print(
            f"[STATIC CHECK] {video_id}: static_fraction={static_fraction:.2f}, "
            f"avg_difference={avg_difference:.2f}, threshold={threshold:.2f}, "
            "source=stream"
        )
        return static_fraction >= min_static_fraction


def main():
    cfg = load_config()
    state = load_state()
    youtube = youtube_client()

    playlist_video_ids = get_playlist_video_ids(youtube, cfg["playlist_id"])
    candidates = []
    state_changed = False

    configured_channels = set(cfg["channels"])

    for old_channel_id in list(state["channels"]):
        if old_channel_id not in configured_channels:
            del state["channels"][old_channel_id]
            state_changed = True

    for channel_id in cfg["channels"]:
        channel_name, uploads_playlist_id = get_channel_uploads_playlist(
            youtube, channel_id
        )
        uploads = get_latest_uploads(
            youtube, uploads_playlist_id, cfg["latest_count"]
        )

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
            print(
                f"[{channel_name}] Initialized. "
                "Existing videos were not imported."
            )
            continue

        last_seen = parse_dt(
            state["channels"][channel_id].get("last_seen_published_at")
        )

        fresh = [
            video for video in uploads
            if parse_dt(video["published_at"]) > last_seen
        ]

        if fresh:
            fresh = add_durations(youtube, fresh)

        for video in fresh:
            duration = video.get("duration_seconds")
            if duration is None:
                print(f"[{channel_name}] Skip (duration unavailable): {video['title']}")
                continue

            if duration < cfg["min_duration_seconds"]:
                print(
                    f"[{channel_name}] Skip < {cfg['min_duration_seconds']/60:g} min: "
                    f"{video['title']} ({duration}s)"
                )
                continue

            if video.get("privacy_status") != "public":
                print(f"[{channel_name}] Skip non-public: {video['title']}")
                continue

            if video["video_id"] in playlist_video_ids:
                print(f"[{channel_name}] Already in playlist: {video['title']}")
                continue

            static_filter = cfg["static_video_filter"]
            if channel_id in static_filter["enabled_channels"]:
                try:
                    static_video = is_static_video(
                        video["video_id"],
                        duration,
                        static_filter,
                    )
                except RuntimeError as exc:
                    print(
                        f"[{channel_name}] Skip (static check unavailable): "
                        f"{video['title']} — {exc}",
                        file=sys.stderr,
                    )
                    continue

                if static_video:
                    print(
                        f"[{channel_name}] Skip static-image video: "
                        f"{video['title']}"
                    )
                    continue

            candidates.append({
                **video,
                "channel_id": channel_id,
                "channel_name": channel_name,
            })

        if parse_dt(newest_seen) > last_seen:
            state["channels"][channel_id] = {
                "channel_name": channel_name,
                "last_seen_published_at": newest_seen,
            }
            state_changed = True

    candidates.sort(key=lambda x: parse_dt(x["published_at"]))

    added = 0
    for video in candidates:
        try:
            add_video_to_playlist(
                youtube, cfg["playlist_id"], video["video_id"]
            )
            playlist_video_ids.add(video["video_id"])
            added += 1
            mins = video["duration_seconds"] / 60
            print(
                f"[ADD] {video['channel_name']}: {video['title']} "
                f"({mins:.1f} min)"
            )
        except HttpError as exc:
            print(
                f"[ERROR] Could not add {video['video_id']}: {exc}",
                file=sys.stderr,
            )
            raise

    if state_changed:
        save_state(state)

    print(f"Done. Added {added} new video(s).")


if __name__ == "__main__":
    main()
