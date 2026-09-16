# Privacy Policy — YT Playlist Formuler

Last updated: September 16, 2026

YT Playlist Formuler is a personal automation tool used to monitor selected public YouTube channels and add eligible videos to a YouTube playlist controlled by the application owner.

## Data accessed

The application uses the YouTube Data API and OAuth authorization to access the YouTube account authorized by the application owner. It may read public YouTube channel and video metadata, inspect the configured destination playlist, and add videos to that playlist.

## Data storage

OAuth credentials and tokens used by the automation are stored as private GitHub Actions secrets and are not intentionally exposed in the public repository. The application stores only synchronization state needed to avoid repeatedly processing the same videos. It does not intentionally store personal YouTube account data in the public repository.

## Data sharing

The application does not sell, rent, or intentionally share user data with third parties. Data is used only to provide the playlist synchronization functionality requested by the application owner. The application necessarily communicates with Google/YouTube APIs and GitHub Actions as part of its operation.

## Data retention and deletion

Synchronization state is retained while the automation is in use. The application owner can stop the automation, delete its stored state and GitHub Actions secrets, and revoke the application's Google account access at any time.

## Contact

Questions about this privacy policy can be submitted through the GitHub repository associated with YT Playlist Formuler.
