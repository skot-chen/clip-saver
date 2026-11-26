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
    net._good.clear()
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
# Proxy list as the user would add it in Settings (the ✓ one first).
H = f'127.0.0.1:{HTTP_PORT}'
S = f'socks5://127.0.0.1:{SOCKS_PORT}'
DEAD = '127.0.0.1:7897'          # nothing listens here (like a proxy app that is off)


def a_switch_off_goes_direct():
    r = extract.inspect(X_POST, False, [H])
    assert r['route'] == '直接连接', r['route']
    assert not SEEN, SEEN
    return r['route']


def b_switch_on_uses_proxy():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'    # like a network where x.com is unreachable
    r = extract.inspect(X_POST, True, [H])
    assert '127.0.0.1:%d' % HTTP_PORT in r['route'] and 'HTTP' in r['route'], r['route']
    r2 = extract.inspect(TH_POST, True, [H])
    assert '127.0.0.1:%d' % HTTP_PORT in r2['route'], r2['route']
    return f"X: {r['route']} | Threads: {r2['route']}"


def c_first_proxy_dead_next_one_used():
    # like the skill log: 7897 does not work, the next one in the list does
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    r = extract.inspect(X_POST, True, [DEAD, S])
    assert '127.0.0.1:%d' % SOCKS_PORT in r['route'] and 'SOCKS5' in r['route'], r['route']
    assert net._good.get('cdn.syndication.twimg.com') == f'socks5://127.0.0.1:{SOCKS_PORT}', net._good
    return r['route'] + '  (remembered for next time)'


def d_wrong_type_is_corrected():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    typed = f'socks5://127.0.0.1:{HTTP_PORT}'
    added = net.check(typed)                       # what "Add proxy" does: test + detect the real type
    assert added['proxy'] == f'http://127.0.0.1:{HTTP_PORT}', added
    r = extract.inspect(X_POST, True, [typed, f'127.0.0.1:{SOCKS_PORT}'])
    assert 'HTTP' in r['route'] and str(HTTP_PORT) in r['route'], r['route']
    return 'typed socks5 on an HTTP port -> ' + r['route']


def e_switch_off_blocked_message():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    try:
        extract.inspect(X_POST, False, [H])
    except extract.ExtractError as e:
        assert '打开「代理」' in str(e), str(e)
        assert not SEEN, 'switch off must not use any proxy'
        assert e.detail, 'raw detail missing'
        return f'{e}  || detail: {e.detail}'
    raise AssertionError('should fail')


def f_proxy_upstream_down_falls_back_to_direct():
    BAD[0] = True                           # the proxy answers "can't reach the site"
    r = extract.inspect(X_POST, True, [H])
    assert r['route'] == '直接连接', r['route']
    return 'proxy says 502 -> ' + r['route']


def g_download_through_socks5():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    r = extract.inspect(X_POST, True, [S])
    url = r['videos'][-1]['variants'][0]['url']
    req = urllib.request.Request(url, headers={'User-Agent': extract.UA, 'Referer': 'https://x.com/'})
    resp, route = net.open_url(req, True, [S])
    with resp:
        data = resp.read()
    assert data[4:8] == b'ftyp' and len(data) > 50_000, len(data)
    assert any('video.twimg.com' in t for _, t in SEEN), SEEN
    return f'{route.describe()} {len(data)} bytes'


def h_everything_down_message():
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    BAD[0] = True
    try:
        extract.inspect(X_POST, True, [DEAD, H])
    except extract.ExtractError as e:
        assert '7897' in str(e) and str(HTTP_PORT) in str(e) and '直接连接' in str(e), str(e)
        return str(e)
    raise AssertionError('should fail')


def i_settings_check_per_site():
    ok = net.check(S)
    assert ok['ok'] and [s['ok'] for s in ok['sites']] == [True, True], ok
    dead = net.check(DEAD)
    assert not dead['ok'] and '没有程序' in dead['error'], dead
    from clipsaver.i18n import set_lang
    set_lang('en')
    try:
        dead_en = net.check(DEAD)
    finally:
        set_lang('zh')
    assert 'nothing is listening' in dead_en['error'], dead_en
    return (f"{ok['address']} {ok['type']}: " + ', '.join(f"{s['name']} {s['ms']}ms" for s in ok['sites'])
            + f" | {dead['address']}: {dead['error']} / {dead_en['error']}")


def j_english_error_message():
    from clipsaver.i18n import set_lang
    os.environ['CLIPSAVER_BLOCK_DIRECT'] = '1'
    set_lang('en')
    try:
        extract.inspect(X_POST, False, [])
    except extract.ExtractError as e:
        assert 'turn Proxy on' in str(e), str(e)
        return str(e)
    finally:
        set_lang('zh')
    raise AssertionError('should fail')


if __name__ == '__main__':
    for p in (HTTP_PORT, SOCKS_PORT, MANUAL_PORT):
        if socket.socket().connect_ex(("127.0.0.1", p)) == 0:
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
