# Progress

## Done (2026-11-26, v0.1.1)
- X + Threads extraction with source labels (main / quote / card), carousels, best quality first.
- Local web UI, downloads to ~/Downloads, optional proxy, no stored data, idle auto-quit.
- 11 offline tests + live smoke test; CI builds ClipSaver.app on macOS arm64 and live-tests it.
- Release: https://github.com/skot-chen/clip-saver/releases/latest

## Done (v0.2.0)
- Ships as ClipSaver-macOS-arm64.dmg (CI checks and mounts it).
- Homepage https://skot-chen.github.io/clip-saver/ from docs/ (screenshots use placeholder thumbnails/names).
- Developer notes live here, not in README (user removed them there).

## Done (v0.3.0 – v0.4.0)
- Windows build (single ClipSaver-Windows.exe); CI tests both builds, release only if both pass.
- Native app window via pywebview (WebKit on Mac, WebView2 on Windows), browser fallback; second launch focuses the window; exit safety net 3 s after close.
- New UI: dark, sidebar (Download / Settings / About) + work area; themes green (default) / purple / orange; Paste button reads the system clipboard.
- No History/Queue/Saved pages on purpose: the user wants no stored data. Only theme + proxy are remembered (localStorage).
- Homepage: click-to-zoom screenshots, OS-aware download buttons, Mac/Windows install tabs.

## Not verified yet
- On the user's own Mac: Gatekeeper first open, how the window looks (CI only proves it opens/closes), "Show in Finder", Paste.
- Icon: option B (black/white split, cross + loop, green download button) — user picked it 2026-11-26.

## Decisions
- No yt-dlp / Playwright: X syndication endpoint + Threads embed page are enough and keep the app ~8 MB.
- Threads embed needs a non-browser User-Agent (browser UA gets the JS app with no video data).
- Release by pushing a new `v*` tag (never move an existing tag).

## Backlog
- Intel Mac build if ever needed.
- If X syndication starts failing: fall back to api.fxtwitter.com.

## v0.5.0 (2026-11-26)
- Icon = the user's own logo design (art/logo-design.png): dark tile, white X + loop, thicker coral arrow. Rebuild with tools/build_icon.py (needs Pillow; art/ files are committed, CI uses them).
- Default theme coral #ff7a45 (key "accent"; only explicit choices saved). Quote label now light blue so it differs from coral.
- tools/make_icon.py is unused now (deleting it was blocked by a safety check; left in place).

## v0.6.0 (2026-11-26)
- Network like Chrome (clipsaver/net.py): system proxy -> user-added -> detected local proxy ports (HTTP or SOCKS5, type sniffed/auto-corrected) -> direct. Switch off = direct only. Working route remembered in memory per host.
- Settings > Network = proxy switch + list with live "可用 · ms / 无法使用" per route (user's iOS-style design).
- Threads videos missing from the embed page (e.g. shared from Instagram) are read by a hidden pywebview window (window mode only). Found via the user's failing share link.
- tests/net_check.py: stand-in HTTP/SOCKS5 proxies, 11 live scenarios; runs in CI. window_check also reads such a Threads post through the hidden window.

## v0.7.0 (2026-11-26)
- User feedback from their Mac: all routes failed in v0.6.1 (likely missing CA certs in the bundled Python) -> certifi bundled, ssl context everywhere.
- Proxies are MANUAL only (user asked: no auto-detect). Telegram-style list: add/delete, click row = first (tick), per-proxy test vs X and Threads. Switch on: tick proxy -> other proxies -> direct (mirrors their skills that tried 7892/7897/7890 in turn). Switch off (default): direct.
- Errors selectable + raw detail + Copy error details (server /api/copy). pywebview text_select=True.
- One main button: Paste link (empty) / Find videos (has text) + 3-step hint.
- 中/EN switch in app (clipsaver/i18n.py for server messages) and on the homepage (both texts in page, html[data-lang]).

## v0.7.1 (2026-11-26)
- Browser-only Threads videos: ~12s -> ~3.5-4.5s. Share links follow redirect without downloading the page; embed and hidden-window read run in parallel (reader cancelled when embed has videos); reader window pre-created at start-up and reused (load_url); stops as soon as a video src appears (0.15s polling, no readyState/extra 1s wait). Normal posts 0.6-1.2s.
- Sidebar network box hidden when Proxy is off (user: direct users do not need it).
