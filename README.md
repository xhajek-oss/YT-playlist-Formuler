# YT-playlist-Formuler

GitHub Actions automation that watches selected YouTube channels and adds newly uploaded videos to one destination playlist.

## Behavior

- Channel IDs or YouTube `@handles` live in `config.yaml`.
- Prefer adding new channels by `@handle` taken directly from the channel URL, for example `https://youtube.com/@chili_tathuy` → `@chili_tathuy`.
- Do not manually guess a `UC...` channel ID for a new channel. The workflow resolves `@handle` to the correct channel ID through the YouTube Data API before running the sync.
- The destination playlist ID and OAuth credentials stay in GitHub Secrets.
- New channels are initialized without importing their old catalogue.
- Later uploads are added automatically.
- Videos shorter than 5 minutes are skipped.
- Duplicate and non-public videos are skipped.
- The workflow runs every 15 minutes and can also be started manually.

## Configure channels

Edit `config.yaml`:

```yaml
min_duration_minutes: 5
latest_videos_per_channel: 15

channels:
  - "@example_channel"
  - "UCxxxxxxxxxxxxxxxx"
```

For a newly added channel, use its `@handle` from the YouTube URL whenever possible. Example:

```text
https://youtube.com/@example_channel?si=...
```

Add this to `config.yaml` as:

```yaml
- "@example_channel"
```

The workflow resolves the handle automatically, so there is no need to look up or guess the case-sensitive `UC...` ID manually.

## Instruction for future ChatGPT sessions

When the user asks to add a YouTube channel to this repository and provides a channel URL, use the `@handle` from that URL in `config.yaml`. Do not search for or guess the channel's `UC...` ID unless there is no usable handle. The workflow is designed to resolve handles automatically before synchronization. Preserve the existing no-backfill behavior: on first initialization, old videos must not be imported.

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

When a channel ID or resolved handle appears in `config.yaml` for the first time, the workflow stores its newest existing upload as a baseline. It does not import the channel's historical videos. Only later uploads are eligible.

## Duration filter

`min_duration_minutes: 5` means:

- `4:59` → skipped
- `5:00` → accepted

The duration is read from YouTube API `contentDetails.duration`.

## State

`state.json` is updated by GitHub Actions and stores the resolved channel ID, channel name, and newest observed upload timestamp. It contains no OAuth credentials or destination playlist ID.

## Run manually

Open **Actions → Sync YouTube playlist → Run workflow**.
