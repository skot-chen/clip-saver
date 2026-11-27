"""Tiny local web server: the UI lives in the browser, the work happens here.

Nothing is saved except the video files themselves. Download jobs live in
memory and disappear when the app quits.
"""
import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, extract, net
from .i18n import set_lang, tr

HERE = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
UI_FILE = HERE / 'clipsaver' / 'ui.html'
DEFAULT_PORT = 8790
IDLE_EXIT_SECONDS = 120   # quit when no browser tab has been open this long

JOBS = {}
JOBS_LOCK = threading.Lock()
LAST_PING = [time.time()]


def save_dir():
    d = Path(os.environ.get('CLIPSAVER_DIR') or Path.home() / 'Downloads')
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_name(name):
    name = re.sub(r'[^\w.-]+', '_', name).strip('._') or 'video'
    return name[:120]


def unique_path(folder, stem, ext='.mp4'):
    p = folder / f'{stem}{ext}'
    n = 2
    while p.exists():
        p = folder / f'{stem}-{n}{ext}'
        n += 1
    return p


def run_download(job_id, url, stem, site, enabled, proxies, lang):
    set_lang(lang)
    job = JOBS[job_id]
    referer = 'https://x.com/' if site == 'x' else 'https://www.threads.com/'
    folder = save_dir()
    target = unique_path(folder, safe_name(stem))
    part = target.with_suffix('.mp4.part')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': extract.UA, 'Referer': referer})
        resp, route = net.open_url(req, enabled, proxies, timeout=12)
        job['route'] = route.describe()
        with resp as r, open(part, 'wb') as f:
            total = int(r.headers.get('Content-Length') or 0)
            job['total'] = total
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                job['done'] += len(chunk)
        if job['done'] == 0:
            raise OSError(tr('下载到的文件是空的', 'the downloaded file is empty'))
        if total and job['done'] < total:
            raise OSError(tr('下载没有完成（网络中断）', 'download incomplete (connection dropped)'))
        part.replace(target)
        job.update(state='ok', path=str(target), name=target.name)
    except Exception as e:  # noqa: BLE001 - shown to the user
        try:
            part.unlink()
        except OSError:
            pass
        detail = getattr(e, 'detail', '') or net.raw(e)
        msg = str(e)
        if 'HTTP Error 403' in msg or 'HTTP Error 410' in msg:
            msg = tr('视频地址过期了，请重新点「查找视频」再下载', 'The video link expired. Press "Find videos" again, then download.')
        elif isinstance(e, net.NetError):
            msg = tr('连不上视频服务器。', 'Could not reach the video server. ') + msg
        elif 'urlopen error' in msg or 'timed out' in msg:
            msg = tr('下载中途网络断了，请再试一次', 'The connection dropped during the download. Please try again.')
        job.update(state='error', error=msg, detail=detail)


def read_clipboard():
    """Text on the system clipboard (used by the Paste button)."""
    try:
        if sys.platform == 'darwin':
            out = subprocess.run(['pbpaste'], capture_output=True, timeout=5).stdout
            return out.decode('utf-8', 'replace').strip()
        if sys.platform.startswith('win'):
            out = subprocess.run(['powershell', '-NoProfile', '-Command',
                                  '[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw'],
                                 capture_output=True, timeout=8, creationflags=0x08000000)  # no console flash
            return out.stdout.decode('utf-8', 'replace').strip()
    except Exception:  # noqa: BLE001
        pass
    return ''


_SYSLANG = []


def system_language():
    """'zh' if the computer's language is Chinese, else 'en' (used until the user picks one)."""
    if _SYSLANG:
        return _SYSLANG[0]
    lang = ''
    try:
        if sys.platform == 'darwin':
            out = subprocess.run(['defaults', 'read', '-g', 'AppleLanguages'], capture_output=True, timeout=3).stdout
            lang = out.decode('utf-8', 'replace').replace('(', '').strip().strip('"').split('"')[0]
        elif sys.platform.startswith('win'):
            import ctypes
            import locale
            lang = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), '')
        if not lang:
            import locale
            lang = locale.getlocale()[0] or os.environ.get('LANG', '')
    except Exception:  # noqa: BLE001
        pass
    _SYSLANG.append('zh' if str(lang).lower().startswith('zh') or 'chinese' in str(lang).lower() else 'en')
    return _SYSLANG[0]


def write_clipboard(text):
    """Put text on the system clipboard (the "Copy error details" button)."""
    try:
        if sys.platform == 'darwin':
            subprocess.run(['pbcopy'], input=text.encode('utf-8'), timeout=5, check=True)
            return True
        if sys.platform.startswith('win'):
            b64 = base64.b64encode(text.encode('utf-8')).decode()
            subprocess.run(['powershell', '-NoProfile', '-Command',
                            f"Set-Clipboard -Value ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b64}')))"],
                           timeout=8, check=True, creationflags=0x08000000)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def open_folder(path):
    if sys.platform == 'darwin':
        subprocess.Popen(['open', path])
    elif sys.platform.startswith('win'):
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(['xdg-open', path])


def reveal(path):
    if sys.platform == 'darwin':
        subprocess.Popen(['open', '-R', path])
    elif sys.platform.startswith('win'):
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        subprocess.Popen(['xdg-open', str(Path(path).parent)])


class Handler(BaseHTTPRequestHandler):
    server_version = 'ClipSaver'

    def log_message(self, format, *args):  # noqa: A002 - quiet console
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get('Content-Length') or 0)
        try:
            return json.loads(self.rfile.read(n) or b'{}')
        except ValueError:
            return {}

    def _local_only(self):
        # Block other websites from driving this server through the browser.
        origin = self.headers.get('Origin')
        host = (self.headers.get('Host') or '').split(':')[0]
        ok_host = host in ('127.0.0.1', 'localhost')
        ok_origin = origin is None or re.match(r'^http://(127\.0\.0\.1|localhost)(:\d+)?$', origin)
        if not (ok_host and ok_origin):
            self._send(403, {'error': 'forbidden'})
            return False
        return True

    def do_GET(self):
        if not self._local_only():
            return
        LAST_PING[0] = time.time()
        if self.path in ('/', '/index.html'):
            self._send(200, UI_FILE.read_bytes(), 'text/html; charset=utf-8')
        elif self.path == '/logo.png':
            self._send(200, (UI_FILE.parent / 'logo.png').read_bytes(), 'image/png')
        elif self.path == '/api/ping':
            self._send(200, {'app': 'clipsaver', 'folder': str(save_dir()), 'version': __version__,
                             'window': bool(WINDOW), 'platform': sys.platform, 'syslang': system_language()})
        elif self.path == '/api/clipboard':
            self._send(200, {'text': read_clipboard()})
        elif self.path.startswith('/api/job/'):
            job = JOBS.get(self.path.rsplit('/', 1)[-1])
            self._send(200 if job else 404, job or {'error': 'no such job'})
        else:
            self._send(404, {'error': 'not found'})

    def do_POST(self):
        if not self._local_only():
            return
        LAST_PING[0] = time.time()
        data = self._json()
        if not isinstance(data, dict):
            data = {}
        lang = 'en' if data.get('lang') == 'en' else 'zh'
        set_lang(lang)
        cfg = data.get('net') if isinstance(data.get('net'), dict) else {}
        enabled = cfg.get('on') is True                       # default: direct, until the user turns it on
        proxies = [str(p) for p in (cfg.get('proxies') or []) if isinstance(p, str)][:20]
        if self.path == '/api/check-proxy':
            if not net.parse_proxy(data.get('proxy')):
                self._send(200, {'ok': False, 'proxy': None,
                                 'error': tr('地址格式不对，例如 127.0.0.1:7890 或 socks5://127.0.0.1:7891',
                                             'Invalid address, e.g. 127.0.0.1:7890 or socks5://127.0.0.1:7891')})
            else:
                self._send(200, net.check(data.get('proxy')))
        elif self.path == '/api/inspect':
            try:
                self._send(200, extract.inspect(data.get('url', ''), enabled, proxies))
            except extract.ExtractError as e:
                self._send(200, {'error': str(e), 'detail': e.detail})
            except Exception as e:  # noqa: BLE001
                self._send(200, {'error': tr('出错了', 'Something went wrong'), 'detail': net.raw(e)})
        elif self.path == '/api/download':
            url = data.get('url') or ''
            if not re.match(r'^https://[\w.-]+\.(twimg\.com|cdninstagram\.com|fbcdn\.net)/', url):
                self._send(400, {'error': tr('不是 X 或 Threads 的视频地址', 'Not an X or Threads video address')})
                return
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = {'id': job_id, 'state': 'running', 'done': 0, 'total': 0}
            threading.Thread(target=run_download, daemon=True, args=(
                job_id, url, data.get('name') or 'video', data.get('site') or 'x', enabled, proxies, lang)).start()
            self._send(200, {'job': job_id})
        elif self.path == '/api/reveal':
            path = data.get('path') or ''
            if path and Path(path).exists() and Path(path).parent == save_dir():
                reveal(path)
            self._send(200, {'ok': True})
        elif self.path == '/api/copy':
            self._send(200, {'ok': write_clipboard(str(data.get('text') or '')[:20000])})
        elif self.path == '/api/open-folder':
            open_folder(str(save_dir()))
            self._send(200, {'ok': True})
        elif self.path == '/api/quit':
            self._send(200, {'ok': True})
            threading.Thread(target=getattr(self.server, 'app_close', self.server.shutdown), daemon=True).start()
        elif self.path == '/api/show':
            bring_to_front()
            self._send(200, {'window': bool(WINDOW)})
        else:
            self._send(404, {'error': 'not found'})


def already_running(port):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f'http://127.0.0.1:{port}/api/ping', timeout=1) as r:
            return json.load(r).get('app') == 'clipsaver'
    except Exception:  # noqa: BLE001
        return False


WINDOW = []   # the app window, when running with one


def bring_to_front():
    for w in WINDOW:
        try:
            w.restore()
            w.show()
        except Exception:  # noqa: BLE001
            pass


def exit_soon(seconds=3):
    # Safety net: if the GUI loop does not wind down by itself, end the process anyway.
    t = threading.Timer(seconds, os._exit, args=(0,))
    t.daemon = True
    t.start()


def close_app(httpd):
    if WINDOW:
        exit_soon()
        for w in WINDOW:
            w.destroy()      # closing the window ends webview.start(), then main() stops the server
    else:
        httpd.shutdown()


READ_JS = r"""
(() => {
  const code = %s;
  const boxes = [...document.querySelectorAll('[data-pressable-container]')];
  // the main post = the first block that links to this post's own code
  const main = boxes.find((b) => b.querySelector('a[href*="/post/' + code + '"]')) || boxes[0];
  const vids = main ? [...main.querySelectorAll('video')] : [];
  return JSON.stringify({here: location.href.includes(code), ready: document.readyState, vids: vids.map((v) => ({
    src: v.currentSrc || v.src || '', poster: v.poster || '',
    w: v.videoWidth || 0, h: v.videoHeight || 0, d: isFinite(v.duration) ? v.duration : 0}))});
})()
"""
READ_LOCK = threading.Lock()
READER = []   # one hidden window made at start-up and reused, so no start-up wait per search


def read_with_browser(page_url, code, cancel=None, timeout=12):
    """Open the post in a hidden window (same engine and system proxy as a
    browser) and return its video addresses as soon as they appear."""
    import webview
    with READ_LOCK:   # one hidden page at a time
        reuse = bool(READER)
        if reuse:
            w = READER[0]
            w.load_url(page_url)
        else:
            w = webview.create_window('ClipSaver reader', page_url, hidden=True)
        best = []
        try:
            end = time.time() + timeout
            while time.time() < end and not (cancel and cancel.is_set()):
                time.sleep(0.15)
                try:
                    r = json.loads(w.evaluate_js(READ_JS % json.dumps(code)) or '{}')
                except Exception:  # noqa: BLE001 - page still loading
                    continue
                if not r.get('here'):
                    continue          # still showing the previous page
                best = [v for v in r.get('vids', []) if v.get('src', '').startswith('https://')]
                if best:
                    break             # found: no extra waiting
        finally:
            try:
                if reuse:
                    w.load_url('about:blank')   # stop the page (and its video) in the background
                else:
                    w.destroy()
            except Exception:  # noqa: BLE001
                pass
        return best


def storage_dir():
    """Where the window keeps its settings: %APPDATA%/ClipSaver on Windows,
    ~/Library/Application Support/ClipSaver on Mac (WebKit uses its own store there)."""
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    path = os.path.join(base, 'ClipSaver')
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path


def run_window(url, httpd):
    """Show the UI in a native app window. Returns False if that is not possible here."""
    try:
        import webview  # pywebview: WebKit on macOS, Edge WebView2 on Windows
    except Exception:  # noqa: BLE001
        return False
    try:
        window = webview.create_window('ClipSaver', url, width=1120, height=780, text_select=True,
                                       min_size=(760, 560), background_color='#0e1014')
        WINDOW.append(window)
        window.events.closed += lambda *a: exit_soon()
        def warm_reader():
            # Make the hidden reader once the main window is up (not during start-up,
            # where it would slow the first window down).
            time.sleep(1.5)
            try:
                if not READER:
                    READER.append(webview.create_window('ClipSaver reader', 'about:blank', hidden=True))
            except Exception:  # noqa: BLE001 - read_with_browser then makes one when needed
                READER.clear()
        window.events.loaded += lambda *a: threading.Thread(target=warm_reader, daemon=True).start()
        extract.browser_reader = read_with_browser
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        # private_mode=False keeps the window's localStorage (proxies, theme, language)
        # after the app closes; pywebview's default private mode wipes it every time.
        webview.start(private_mode=False, storage_path=storage_dir())
    except Exception:  # noqa: BLE001 - e.g. WebView2 missing: fall back to the browser
        WINDOW.clear()
        extract.browser_reader = None
        return False
    httpd.shutdown()
    return True


def main():
    port = int(os.environ.get('CLIPSAVER_PORT') or DEFAULT_PORT)
    headless = bool(os.environ.get('CLIPSAVER_NO_BROWSER'))   # tests: server only
    if already_running(port):
        if not headless:
            try:   # ask the running copy to show its window
                req = urllib.request.Request(f'http://127.0.0.1:{port}/api/show', data=b'{}',
                                             headers={'Content-Type': 'application/json'})
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=2) as r:
                    if json.load(r).get('window'):
                        return
            except Exception:  # noqa: BLE001
                pass
            webbrowser.open(f'http://127.0.0.1:{port}/')
        return
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)  # port taken: any free one
    httpd.app_close = lambda: close_app(httpd)
    url = f'http://127.0.0.1:{httpd.server_address[1]}/'
    if sys.stdout:  # None inside a windowed (no console) build
        print(f'ClipSaver running at {url}', flush=True)

    if not headless and not os.environ.get('CLIPSAVER_BROWSER') and run_window(url, httpd):
        return   # window was closed

    # Browser mode (fallback) or headless test mode.
    def idle_watch():
        while True:
            time.sleep(10)
            busy = any(j['state'] == 'running' for j in JOBS.values())
            if not busy and time.time() - LAST_PING[0] > IDLE_EXIT_SECONDS:
                httpd.shutdown()
                return
    if not os.environ.get('CLIPSAVER_NO_IDLE_EXIT'):
        threading.Thread(target=idle_watch, daemon=True).start()
    if not headless:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    httpd.serve_forever()


if __name__ == '__main__':
    main()
