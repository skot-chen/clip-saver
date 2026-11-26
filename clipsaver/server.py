"""Tiny local web server: the UI lives in the browser, the work happens here.

Nothing is saved except the video files themselves. Download jobs live in
memory and disappear when the app quits.
"""
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


def run_download(job_id, url, stem, site, enabled, manual):
    job = JOBS[job_id]
    referer = 'https://x.com/' if site == 'x' else 'https://www.threads.com/'
    folder = save_dir()
    target = unique_path(folder, safe_name(stem))
    part = target.with_suffix('.mp4.part')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': extract.UA, 'Referer': referer})
        resp, route = net.open_url(req, enabled, manual, timeout=30)
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
            raise OSError('下载到的文件是空的')
        if total and job['done'] < total:
            raise OSError('下载没有完成（网络中断）')
        part.replace(target)
        job.update(state='ok', path=str(target), name=target.name)
    except Exception as e:  # noqa: BLE001 - shown to the user
        try:
            part.unlink()
        except OSError:
            pass
        msg = str(e)
        if 'HTTP Error 403' in msg or 'HTTP Error 410' in msg:
            msg = '视频地址过期了，请重新点「查找」再下载'
        elif isinstance(e, net.NetError):
            msg = '连不上视频服务器。' + msg
        elif 'urlopen error' in msg or 'timed out' in msg:
            msg = '下载中途网络断了，请再试一次'
        job.update(state='error', error=msg)


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
                             'window': bool(WINDOW), 'platform': sys.platform})
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
        cfg = data.get('net') if isinstance(data.get('net'), dict) else {}
        enabled = cfg.get('on', True) is not False
        manual = [str(p) for p in (cfg.get('proxies') or []) if isinstance(p, str)][:20]
        if self.path == '/api/net':
            self._send(200, net.status(enabled, manual))
        elif self.path == '/api/check-proxy':
            p = net.fix_type(net.parse_proxy(data.get('proxy')))
            if not p:
                self._send(200, {'ok': False, 'error': '地址格式不对，例如 127.0.0.1:7890 或 socks5://127.0.0.1:7891'})
            else:
                self._send(200, {'proxy': p, **net.check(net.Route('manual', p))})
        elif self.path == '/api/inspect':
            try:
                self._send(200, extract.inspect(data.get('url', ''), enabled, manual))
            except extract.ExtractError as e:
                self._send(200, {'error': str(e)})
            except Exception as e:  # noqa: BLE001
                self._send(200, {'error': f'出错了：{e}'})
        elif self.path == '/api/download':
            url = data.get('url') or ''
            if not re.match(r'^https://[\w.-]+\.(twimg\.com|cdninstagram\.com|fbcdn\.net)/', url):
                self._send(400, {'error': '不是 X 或 Threads 的视频地址'})
                return
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = {'id': job_id, 'state': 'running', 'done': 0, 'total': 0}
            threading.Thread(target=run_download, daemon=True, args=(
                job_id, url, data.get('name') or 'video', data.get('site') or 'x', enabled, manual)).start()
            self._send(200, {'job': job_id})
        elif self.path == '/api/reveal':
            path = data.get('path') or ''
            if path and Path(path).exists() and Path(path).parent == save_dir():
                reveal(path)
            self._send(200, {'ok': True})
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
  return JSON.stringify({ready: document.readyState, vids: vids.map((v) => ({
    src: v.currentSrc || v.src || '', poster: v.poster || '',
    w: v.videoWidth || 0, h: v.videoHeight || 0, d: isFinite(v.duration) ? v.duration : 0}))});
})()
"""
READ_LOCK = threading.Lock()


def read_with_browser(page_url, code, timeout=20):
    """Open the post in a hidden window (same engine and system proxy as a
    browser), wait for its video to load, and return the video addresses."""
    import webview
    with READ_LOCK:   # one hidden window at a time
        w = webview.create_window('ClipSaver reader', page_url, hidden=True)
        best = []
        try:
            end = time.time() + timeout
            while time.time() < end:
                time.sleep(0.5)
                try:
                    r = json.loads(w.evaluate_js(READ_JS % json.dumps(code)) or '{}')
                except Exception:  # noqa: BLE001 - page still loading
                    continue
                vids = [v for v in r.get('vids', []) if v.get('src', '').startswith('https://')]
                if vids:
                    best = vids
                    if r.get('ready') == 'complete':
                        time.sleep(1.0)    # let other videos of a carousel appear too
                        try:
                            r = json.loads(w.evaluate_js(READ_JS % json.dumps(code)) or '{}')
                            more = [v for v in r.get('vids', []) if v.get('src', '').startswith('https://')]
                            best = more if len(more) > len(best) else best
                        except Exception:  # noqa: BLE001
                            pass
                        break
        finally:
            try:
                w.destroy()
            except Exception:  # noqa: BLE001
                pass
        return best


def run_window(url, httpd):
    """Show the UI in a native app window. Returns False if that is not possible here."""
    try:
        import webview  # pywebview: WebKit on macOS, Edge WebView2 on Windows
    except Exception:  # noqa: BLE001
        return False
    try:
        window = webview.create_window('ClipSaver', url, width=1120, height=780,
                                       min_size=(760, 560), background_color='#0e1014')
        WINDOW.append(window)
        window.events.closed += lambda *a: exit_soon()
        extract.browser_reader = read_with_browser
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        webview.start()
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
