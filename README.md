# YT-playlist-Formuler

GitHub Actions automation that watches selected YouTube channels and adds newly uploaded videos to one destination playlist.

## Behavior

- Channel IDs live in `config.yaml`.
- The destination playlist ID and OAuth credentials stay in GitHub Secrets.
- New channels are initialized without importing their old catalogue.
- Later uploads are added automatically.
- Videos shorter than 5 minutes are skipped.
- Duplicate and non-public videos are skipped.
- The workflow runs daily and can also be started manually.

## Configure channels

Edit `config.yaml`:

```yaml
min_duration_minutes: 5
latest_videos_per_channel: 15

channels:
  - "UCxxxxxxxxxxxxxxxx"
  - "UCyyyyyyyyyyyyyyyy"
```

To follow another channel, add one more channel ID and commit the change.

## Required GitHub Secrets

Open **Repository → Settings → Secrets and variables → Actions** and create:

- `YOUTUBE_CLIENT_ID`
- `YOUTUBE_CLIENT_SECRET`
- `YOUTUBE_REFRESH_TOKEN`
- `YOUTUBE_PLAYLIST_ID`

## Google Cloud / OAuth setup

1. Create a Google Cloud project.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen.
4. Create an OAuth client of type **Desktop app**.
5. Download the credentials JSON and rename it to `client_secret.json`.
6. Keep `client_secret.json` only on your computer; `.gitignore` excludes it.

Install dependencies and generate the refresh token:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python get_refresh_token.py
```

On Windows PowerShell, activate with:

```powershell
.venv\Scripts\Activate.ps1
```

The helper prints the values for the three OAuth GitHub Secrets.

## First run for a channel

When a channel ID appears in `config.yaml` for the first time, the workflow stores its newest existing upload as a baseline. It does not import the channel's historical videos. Only later uploads are eligible.

## Duration filter

`min_duration_minutes: 5` means:

- `4:59` → skipped
- `5:00` → accepted

The duration is read from YouTube API `contentDetails.duration`.

## State

`state.json` is updated by GitHub Actions and stores the channel ID, channel name, and newest observed upload timestamp. It contains no OAuth credentials or destination playlist ID.

## Run manually

Open **Actions → Sync YouTube playlist → Run workflow**.
