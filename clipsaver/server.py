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

from . import extract

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


def run_download(job_id, url, stem, site, proxy):
    job = JOBS[job_id]
    referer = 'https://x.com/' if site == 'x' else 'https://www.threads.com/'
    opener = extract.make_opener(proxy)
    folder = save_dir()
    target = unique_path(folder, safe_name(stem))
    part = target.with_suffix('.mp4.part')
    try:
        req = urllib.request.Request(url, headers={'User-Agent': extract.UA, 'Referer': referer})
        with opener.open(req, timeout=30) as r, open(part, 'wb') as f:
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
            msg = '视频地址过期了，请重新点「查找视频」再下载'
        elif 'urlopen error' in msg or 'timed out' in msg:
            msg = '连不上视频服务器。如果需要代理，请在设置里填写后重试'
        job.update(state='error', error=msg)


def reveal(path):
    if sys.platform == 'darwin':
        subprocess.Popen(['open', '-R', path])
    elif sys.platform.startswith('win'):
        subprocess.Popen(['explorer', '/select,', path])
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
        elif self.path == '/api/ping':
            self._send(200, {'app': 'clipsaver', 'folder': str(save_dir())})
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
        proxy = (data.get('proxy') or '').strip() or None
        if proxy and not re.match(r'^(https?|socks5h?)://', proxy):
            proxy = 'http://' + proxy
        if self.path == '/api/inspect':
            try:
                self._send(200, extract.inspect(data.get('url', ''), proxy))
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
                job_id, url, data.get('name') or 'video', data.get('site') or 'x', proxy)).start()
            self._send(200, {'job': job_id})
        elif self.path == '/api/reveal':
            path = data.get('path') or ''
            if path and Path(path).exists() and Path(path).parent == save_dir():
                reveal(path)
            self._send(200, {'ok': True})
        elif self.path == '/api/quit':
            self._send(200, {'ok': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._send(404, {'error': 'not found'})


def already_running(port):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                f'http://127.0.0.1:{port}/api/ping', timeout=1) as r:
            return json.load(r).get('app') == 'clipsaver'
    except Exception:  # noqa: BLE001
        return False


def main():
    port = int(os.environ.get('CLIPSAVER_PORT') or DEFAULT_PORT)
    open_browser = not os.environ.get('CLIPSAVER_NO_BROWSER')
    if already_running(port):
        if open_browser:
            webbrowser.open(f'http://127.0.0.1:{port}/')
        return
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    except OSError:
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)  # port taken: any free one
    url = f'http://127.0.0.1:{httpd.server_address[1]}/'
    print(f'ClipSaver running at {url}', flush=True)

    def idle_watch():
        while True:
            time.sleep(10)
            busy = any(j['state'] == 'running' for j in JOBS.values())
            if not busy and time.time() - LAST_PING[0] > IDLE_EXIT_SECONDS:
                httpd.shutdown()
                return
    if not os.environ.get('CLIPSAVER_NO_IDLE_EXIT'):
        threading.Thread(target=idle_watch, daemon=True).start()
    if open_browser:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    httpd.serve_forever()


if __name__ == '__main__':
    main()
