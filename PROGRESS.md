# Progress

## Done (2026-11-26, v0.1.1)
- X + Threads extraction with source labels (main / quote / card), carousels, best quality first.
- Local web UI, downloads to ~/Downloads, optional proxy, no stored data, idle auto-quit.
- 11 offline tests + live smoke test; CI builds ClipSaver.app on macOS arm64 and live-tests it.
- Release: https://github.com/skot-chen/clip-saver/releases/latest

## Not verified yet
- First-open on the user's Mac (Gatekeeper prompt), browser auto-open, "Show in Finder".

## Decisions
- No yt-dlp / Playwright: X syndication endpoint + Threads embed page are enough and keep the app ~8 MB.
- Threads embed needs a non-browser User-Agent (browser UA gets the JS app with no video data).
- Release by pushing a new `v*` tag (never move an existing tag).

## Backlog
- Intel Mac build if ever needed.
- If X syndication starts failing: fall back to api.fxtwitter.com.
