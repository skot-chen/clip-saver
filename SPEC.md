# ClipSaver – SPEC

Mac tool to download videos from X and Threads posts. Merges two earlier Claude skills
(threads-downloader, x-video-downloader) into one double-click app.

## Requirements (from the user)
- Mac app, packaged and downloadable from GitHub (Releases link).
- Direct network by default; proxy only as an optional setting.
- Posts with several videos: list them and let the user choose.
- Confusing posts (video belongs to a quoted post / card, decoy elements): detect and label where each video comes from.
- No local data storage (no history/db). Only the downloaded files.

## Acceptance
- [x] X: main / quoted / card videos found and labelled; best mp4 first; photos ignored.
- [x] Threads: single, carousel, quoted, quoted-carousel posts; photo-only → clear message.
- [x] Downloads are complete mp4 files with audio, unique names, saved to ~/Downloads.
- [x] Clear Chinese error messages (bad link, deleted, no video, network, wrong proxy, expired URL).
- [x] Server refuses requests from other websites (Origin/Host check).
- [x] Packaged app built and live-tested on a real macOS runner in CI.
- [ ] User confirms it works on their Mac.
