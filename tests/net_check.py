"""Live checks of the network routes (needs internet).

Starts small stand-ins for proxy apps' local ports - an HTTP one (like Clash's
7890) and a SOCKS5-only one (like 7891 / Shadowsocks) - that really tunnel
traffic and record what went through them, then checks each situation:

    python tests/net_check.py
"""
import os
import socket
import socketserver
import struct
import sys
import threading
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from clipsaver import extract, net  # noqa: E402

HTTP_PORT, SOCKS_PORT, MANUAL_PORT = 7890, 7891, 18089   # 7890/7891 are probed, 18089 is not
SEEN = []          # (port, target) that went through a stand-in proxy
BAD = [False]      # True: proxies answer "can't reach the site" (like an upstream outage)


def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def tunnel(client, host, port):
    upstream = socket.create_connection((host, port), timeout=15)
    t = threading.Thread(target=pump, args=(upstream, client), daemon=True)
    t.start()
    pump(client, upstream)
    t.join(5)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port, kind):
        self.kind = kind
        super().__init__(('127.0.0.1', port), Handler)

    def handle_error(self, request, client_address):
        pass   # port probes simply connect and hang up


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        c = self.request
        c.settimeout(15)
        first = c.recv(1, socket.MSG_PEEK)
        if not first:
            return
        port = self.server.server_address[1]
        if first == b'\x05' and self.server.kind in ('socks', 'mixed'):
            c.recv(3)                                   # greeting
            c.sendall(b'\x05\x00')
            head = c.recv(4)
            if len(head) < 4:
                return
            if head[3] == 3:
                n = c.recv(1)[0]
                host = c.recv(n).decode()
            else:
                host = socket.inet_ntoa(c.recv(4))
            tport = struct.unpack('>H', c.recv(2))[0]
            SEEN.append((port, f'{host}:{tport}'))
            if BAD[0] or host == '127.0.0.1':
                c.sendall(b'\x05\x04\x00\x01' + b'\0' * 6)
                return
            c.sendall(b'\x05\x00\x00\x01' + b'\0' * 6)
            tunnel(c, host, tport)
            return
        if self.server.kind not in ('http', 'mixed'):
            return                                      # SOCKS-only port ignores HTTP
        buf = b''
        while b'\r\n\r\n' not in buf:
            chunk = c.recv(4096)
            if not chunk:
                return
            buf += chunk
        method, target = buf.split(b'\r\n')[0].decode('latin-1').split()[:2]
        SEEN.append((port, target))
        if BAD[0] or method != 'CONNECT' or target.startswith('127.0.0.1'):
            c.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
            return
        host, tport = target.rsplit(':', 1)
        c.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
        tunnel(c, host, int(tport))


def reset():
    SEEN.clear()
    BAD[0] = False
    net._remembered.clear()
    net._last_good[0] = None
    for k in ('CLIPSAVER_BLOCK_DIRECT', 'HTTPS_PROXY', 'HTTP_PROXY', 'https_proxy', 'http_proxy', 'ALL_PROXY'):
        os.environ.pop(k, None)


X_POST = 'https://x.com/a/status/1578353380363501568'
TH_POST = 'https://www.threads.com/@natgeo/post/DgI5S-xHSVS'
servers = {}
results = []


def stop(port):
    s = servers.pop(port)
    s.shutdown()
    s.server_close()


def start(port, kind):
    s = Server(port, kind)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    servers[port] = s


def ports_seen():
    return sorted({p for p, _ in SEEN})


# ---------------------------------------------------------------- scenarios

def a_open_network_no_proxy_app_goes_direct():
    # no proxy app running, open network (or a proxy app in TUN mode)
    for p in (HTTP_PORT, SOCKS_PORT):
        stop(p)
    try:
        r = extract.inspect(X_POST)
        assert r['route'] == '直接连接', r['route']
        assert not SEEN, SEEN
        return r['route']
    finally:
        start(HTTP_PORT, 'http')
        start(SOCKS_PORT, 'socks')


def a2_proxy_app_running_is_preferred():
    # proxy app running but not set as system proxy: use it before direct,
    # so blocked sites do not first wait for a direct-connection timeout
    r = extract.inspect(X_POST)
    assert '127.0.0.1:%d' % HTTP_PORT in r['route'], r['route']
    return r['route']


def b_blocked_finds_local_http_proxy():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'    # like a network where x.com is unreachable
    r = extract.inspect(X_POST)
    assert '127.0.0.1:%d' % HTTP_PORT in r['route'] and 'HTTP' in r['route'], r['route']
    r2 = extract.inspect(TH_POST)
    assert '127.0.0.1' in r2['route'], r2['route']
    return f"X: {r['route']} | Threads: {r2['route']}"


def c_blocked_only_socks5_running():
    stop(HTTP_PORT)
    try:
        os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
        r = extract.inspect(X_POST)
        assert '127.0.0.1:%d' % SOCKS_PORT in r['route'] and 'SOCKS5' in r['route'], r['route']
        assert SOCKS_PORT in ports_seen(), SEEN
        return r['route']
    finally:
        start(HTTP_PORT, 'http')


def d_system_proxy_used_first():
    os.environ['HTTPS_PROXY'] = f'http://127.0.0.1:{HTTP_PORT}'   # read by getproxies(), like the OS setting
    r = extract.inspect(X_POST)
    assert r['route'].startswith('系统代理 127.0.0.1:%d' % HTTP_PORT), r['route']
    return r['route']


def e_user_added_proxy():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    stop(HTTP_PORT)
    stop(SOCKS_PORT)
    try:
        r = extract.inspect(X_POST, True, [f'socks5://127.0.0.1:{MANUAL_PORT}'])
        assert r['route'].startswith('手动代理 127.0.0.1:%d' % MANUAL_PORT), r['route']
        return r['route']
    finally:
        start(HTTP_PORT, 'http')
        start(SOCKS_PORT, 'socks')


def f_switch_off_blocked_message():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    try:
        extract.inspect(X_POST, False)
    except extract.ExtractError as e:
        assert '代理」开关' in str(e), str(e)
        assert not SEEN, 'switch off must not use any proxy'
        return str(e)
    raise AssertionError('should fail')


def g_system_proxy_upstream_down_falls_back():
    os.environ['HTTPS_PROXY'] = f'http://127.0.0.1:{HTTP_PORT}'
    BAD[0] = True                           # all stand-ins answer "can't reach"
    r = extract.inspect(X_POST)
    assert r['route'] == '直接连接', r['route']
    return 'system proxy says 502 -> ' + r['route']


def h_download_through_socks5():
    stop(HTTP_PORT)
    try:
        os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
        r = extract.inspect(X_POST)
        url = r['videos'][-1]['variants'][0]['url']
        req = urllib.request.Request(url, headers={'User-Agent': extract.UA, 'Referer': 'https://x.com/'})
        resp, route = net.open_url(req)
        with resp:
            data = resp.read()
        assert data[4:8] == b'ftyp' and len(data) > 50_000, len(data)
        assert any('video.twimg.com' in t for _, t in SEEN), SEEN
        return f'{route.describe()} {len(data)} bytes'
    finally:
        start(HTTP_PORT, 'http')


def i_everything_down_message():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    BAD[0] = True
    try:
        extract.inspect(X_POST)
    except extract.ExtractError as e:
        assert '直接连接' in str(e) and '127.0.0.1:%d' % HTTP_PORT in str(e), str(e)
        return str(e)
    raise AssertionError('should fail')


def j_settings_list_status():
    stop(SOCKS_PORT)
    start(SOCKS_PORT, 'socks')
    st = net.status(True, ['127.0.0.1:9', f'socks5://127.0.0.1:{SOCKS_PORT}'])
    rows = {(r['kind'], r['address'], r['type']): r['ok'] for r in st['routes']}
    assert rows.get(('manual', '127.0.0.1:9', 'HTTP')) is False, rows
    assert rows.get(('manual', f'127.0.0.1:{SOCKS_PORT}', 'SOCKS5')) is True, rows
    assert rows.get(('local', f'127.0.0.1:{HTTP_PORT}', 'HTTP')) is True, rows
    assert rows.get(('direct', '', '')) is True, rows
    return '; '.join(f"{r['describe']}={'可用 %dms' % r['ms'] if r['ok'] else '无法使用'}" for r in st['routes'])


if __name__ == '__main__':
    for p in (HTTP_PORT, SOCKS_PORT, MANUAL_PORT):
        if net.port_open(p):
            sys.exit(f'port {p} is already in use; stop that program first')
    start(HTTP_PORT, 'http')
    start(SOCKS_PORT, 'socks')
    start(MANUAL_PORT, 'mixed')
    for name, fn in list(globals().items()):
        if name[0] in 'abcdefghij' and '_' in name[1:3] and callable(fn) and name not in ('extract', 'net'):
            reset()
            net._kind_cache.clear()
            try:
                results.append((True, name, fn()))
            except AssertionError as e:
                results.append((False, name, str(e)))
            except Exception as e:  # noqa: BLE001
                results.append((False, name, f'{type(e).__name__}: {e}'))
    for p in list(servers):
        stop(p)
    bad = 0
    for ok, name, msg in results:
        print('OK  ' if ok else 'FAIL', name, '-', msg)
        bad += not ok
    print('failures:', bad)
    sys.exit(1 if bad else 0)
