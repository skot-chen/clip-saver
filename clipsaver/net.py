"""How ClipSaver reaches the internet - chosen by the user, like Telegram's
proxy settings:

  Proxy switch OFF  -> direct connection.
  Proxy switch ON   -> the proxy the user picked (✓) from the list they added.

Proxies are added by hand (127.0.0.1:7890, socks5://127.0.0.1:7891 ...).
HTTP and SOCKS5 both work; if the typed type is wrong for that port, the real
one is detected and used. Nothing is detected automatically and nothing is
written to disk here (the page keeps the list).

HTTPS certificates are checked against the bundled certifi list, so the app
does not depend on certificate files being present on the computer.
"""
import http.client
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .i18n import get_lang, set_lang, tr

TEST_SITES = [
    ('X', 'https://cdn.syndication.twimg.com/tweet-result?id=20&token=x'),
    ('Threads', 'https://www.threads.com/robots.txt'),
]
_kind_cache = {}


class NetError(Exception):
    """The connection failed; the message can be shown to the user as-is."""

    def __init__(self, message, detail=''):
        super().__init__(message)
        self.detail = detail


def ssl_context():
    ctx = ssl.create_default_context()
    try:
        import certifi   # bundled list of trusted certificates
        ctx.load_verify_locations(certifi.where())
    except Exception:  # noqa: BLE001 - fall back to the system's own list
        pass
    return ctx


# ---------------------------------------------------------------- SOCKS5

def socks5_connect(proxy_host, proxy_port, host, port, timeout):
    """Open a TCP connection to host:port through a SOCKS5 proxy (no auth)."""
    s = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        s.sendall(b'\x05\x01\x00')
        if s.recv(2) != b'\x05\x00':
            raise OSError('SOCKS5 proxy refused (needs a password?)')
        name = host.encode('idna')
        s.sendall(b'\x05\x01\x00\x03' + bytes([len(name)]) + name + struct.pack('>H', port))
        head = _recv_exact(s, 4)
        if head[1] != 0:
            raise OSError(f'SOCKS5 proxy could not reach the site (code {head[1]})')
        skip = {1: 4, 3: None, 4: 16}[head[3]]
        if skip is None:
            skip = _recv_exact(s, 1)[0]
        _recv_exact(s, skip + 2)
        return s
    except Exception:
        s.close()
        raise


def _recv_exact(s, n):
    data = b''
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise OSError('SOCKS5 proxy closed the connection')
        data += chunk
    return data


def _socks_opener(host, port):
    ctx = ssl_context()

    class Conn(http.client.HTTPConnection):
        def connect(self):
            self.sock = socks5_connect(host, port, self.host, self.port, self.timeout)

    class SConn(http.client.HTTPSConnection):
        def connect(self):
            raw = socks5_connect(host, port, self.host, self.port, self.timeout)
            self.sock = ctx.wrap_socket(raw, server_hostname=self.host)

    class H(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(Conn, req)

    class HS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(SConn, req)

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), H, HS)


# ---------------------------------------------------------------- routes

class Route:
    def __init__(self, proxy=None):
        self.proxy = proxy   # None = direct, else scheme://host:port

    @property
    def scheme(self):
        return urllib.parse.urlsplit(self.proxy).scheme if self.proxy else ''

    @property
    def hostport(self):
        return urllib.parse.urlsplit(self.proxy).netloc if self.proxy else ''

    def describe(self):
        if not self.proxy:
            return tr('直接连接', 'Direct connection')
        return tr('代理 ', 'Proxy ') + f'{self.hostport} ({"SOCKS5" if self.scheme == "socks5" else "HTTP"})'

    def opener(self):
        https = urllib.request.HTTPSHandler(context=ssl_context())
        if not self.proxy:
            return urllib.request.build_opener(urllib.request.ProxyHandler({}), https)
        if self.scheme == 'socks5':
            h, p = self.hostport.rsplit(':', 1)
            return _socks_opener(h, int(p))
        return urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': self.proxy, 'https': self.proxy}), https)


def parse_proxy(value):
    """'127.0.0.1:7890' / 'http://…' / 'socks5://…' -> normalised URL, or None."""
    if not value:
        return None
    value = value.strip()
    if '://' not in value:
        value = 'http://' + value
    try:
        u = urllib.parse.urlsplit(value)
        port = u.port
    except ValueError:
        return None
    scheme = {'http': 'http', 'https': 'http', 'socks5': 'socks5', 'socks5h': 'socks5', 'socks': 'socks5'}.get(u.scheme.lower())
    if not scheme or not u.hostname or not port:
        return None
    return f'{scheme}://{u.hostname}:{port}'


def sniff(host, port):
    """Which protocol a proxy port speaks: 'http', 'socks5' or None."""
    key = (host, port)
    if key in _kind_cache:
        return _kind_cache[key]
    kind = None
    try:   # HTTP proxies answer a CONNECT line with "HTTP/1.x ..."
        with socket.create_connection((host, port), timeout=1.5) as s:
            s.settimeout(1.5)
            s.sendall(b'CONNECT 127.0.0.1:9 HTTP/1.1\r\nHost: 127.0.0.1:9\r\n\r\n')
            if s.recv(5).startswith(b'HTTP/'):
                kind = 'http'
    except OSError:
        pass
    if not kind:
        try:   # SOCKS5 proxies answer the greeting with 05 00
            with socket.create_connection((host, port), timeout=1.5) as s:
                s.settimeout(1.5)
                s.sendall(b'\x05\x01\x00')
                if s.recv(2) == b'\x05\x00':
                    kind = 'socks5'
        except OSError:
            pass
    if kind:
        _kind_cache[key] = kind
    return kind


def fix_type(proxy):
    """If the port actually speaks the other protocol, use that one."""
    if not proxy:
        return proxy
    u = urllib.parse.urlsplit(proxy)
    real = sniff(u.hostname, u.port)
    return f'{real}://{u.hostname}:{u.port}' if real and real != u.scheme else proxy


def known_type(proxy):
    """Like fix_type, but only uses what an earlier test already found out (no network)."""
    if not proxy:
        return proxy
    u = urllib.parse.urlsplit(proxy)
    real = _kind_cache.get((u.hostname, u.port))
    return f'{real}://{u.hostname}:{u.port}' if real and real != u.scheme else proxy


_lock = threading.Lock()
_good = {}   # host -> route key that worked last time (memory only)


def routes(enabled, proxies):
    """Switch on: the user's proxies (the ✓ one is first in the list), then
    direct as the last resort - like the old skills, which tried 7892, 7897,
    7890 in turn and downloaded Threads videos directly.  Switch off: direct."""
    out, seen = [], set()
    if enabled:
        for p in proxies or ():
            p = known_type(parse_proxy(p))   # no probing here: it would slow every request down
            if p and p not in seen:
                seen.add(p)
                out.append(Route(p))
    out.append(Route())
    return out


def key(route):
    return route.proxy or 'direct'


def _fresh(req):
    # A Request is changed by the proxy handler, so every attempt gets its own copy.
    return urllib.request.Request(req.full_url, data=req.data, headers=dict(req.header_items()),
                                  method=req.get_method())


def open_url(req, enabled=False, proxies=(), timeout=8):
    """Open a request, trying routes in turn. Returns (response, route).

    HTTP errors (404, 403…) from the website itself are raised unchanged.
    Network failures (and proxies answering 407/502/503/504) move on to the
    next route; if all fail, NetError lists every route with its reason.
    """
    host = urllib.parse.urlsplit(req.full_url).hostname or ''
    cands = routes(enabled, proxies)
    first = _good.get(host)
    cands.sort(key=lambda r: key(r) != first)        # last success for this site first
    many = len(cands) > 1
    tried = []
    for route in cands:
        try:
            if not route.proxy and os.environ.get('CLIPSAVER_BLOCK_DIRECT'):
                raise urllib.error.URLError('blocked (test)')   # tests: pretend the site is unreachable directly
            # A blocked site usually hangs while connecting; give up on a route after
            # `timeout` seconds of silence (it is a per-step limit, not a total).
            resp = route.opener().open(_fresh(req), timeout=min(timeout, 6) if many else timeout)
        except urllib.error.HTTPError as e:
            if route.proxy and e.code in (407, 502, 503, 504):
                tried.append((route, f'HTTP {e.code} from proxy'))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as e:
            tried.append((route, raw(e)))
            continue
        with _lock:
            _good[host] = key(route)
        return resp, route
    detail = ' | '.join(f'{r.describe()}: {d}' for r, d in tried)
    raise NetError(failure_message(enabled, tried, host), detail)


def raw(e):
    reason = getattr(e, 'reason', e)
    return f'{type(reason).__name__}: {reason}'


def failure_message(enabled, tried, host=''):
    lines = '；'.join(f'{r.describe()}（{friendly(d)}）' for r, d in tried) if get_lang() != 'en' \
        else '; '.join(f'{r.describe()} ({friendly(d)})' for r, d in tried)
    if not enabled:
        return tr(f'连不上 {host}：{lines}。如果你平时用代理软件（Clash、V2Ray、Shadowsocks 等），'
                  '请到「设置 → 网络」打开「代理」，再添加你的代理端口。',
                  f'Could not reach {host}: {lines}. If you use a proxy app (Clash, V2Ray, Shadowsocks…), '
                  'go to Settings → Network, turn Proxy on and add your proxy port.')
    return tr(f'试过这些线路都连不上 {host}：{lines}。请确认代理软件开着、端口正确；'
              '如果代理软件是「规则」模式，可以试试切到「全局」模式。',
              f'None of these routes could reach {host}: {lines}. Check the proxy app is running and the '
              'ports are right; if it is in "Rule" mode, try "Global" mode.')


def friendly(detail):
    s = str(detail)
    low = s.lower()
    if 'certificate' in low:
        return tr('证书校验失败', 'certificate check failed')
    if 'refused' in low or '10061' in s or 'errno 61' in low:
        return tr('这个端口没有程序在用', 'nothing is listening on that port')
    if 'timed out' in low or 'timeout' in low:
        return tr('超时', 'timed out')
    if 'socks5 proxy refused' in low:
        return tr('不是 SOCKS5 端口，或需要密码', 'not a SOCKS5 port, or it needs a password')
    if 'socks5 proxy could not reach' in low or 'tunnel connection failed' in low or 'from proxy' in low:
        return tr('代理连不上这个网站，检查代理软件的节点或规则', 'the proxy could not reach the site — check its server or rules')
    if 'remote end closed' in low or 'badstatusline' in low:
        return tr('端口类型不对', 'wrong proxy type for this port')
    if 'getaddrinfo' in low or 'nodename' in low or 'name or service' in low or '11001' in s:
        return tr('网址解析失败（DNS）', 'DNS lookup failed')
    if 'eof' in low or 'reset' in low or '10054' in s or 'errno 54' in low:
        return tr('连接被中断（可能被拦截）', 'connection was cut (possibly blocked)')
    return tr('网络错误', 'network error')


# ---------------------------------------------------------------- settings page

def check(proxy, timeout=6):
    """Test one proxy against X and Threads -> what the Settings row shows."""
    p = fix_type(parse_proxy(proxy))
    if not p:
        return {'ok': False, 'error': tr('地址格式不对', 'invalid address'), 'detail': '', 'sites': [], 'proxy': None}
    route = Route(p)
    lang = get_lang()
    sites = [None] * len(TEST_SITES)

    def run(i, name, url):
        set_lang(lang)
        t0 = time.time()
        req = urllib.request.Request(url, headers={'User-Agent': 'ClipSaver/1.0'})
        try:
            with route.opener().open(req, timeout=timeout) as r:
                r.read(1)
            ok, detail = True, ''
        except urllib.error.HTTPError as e:
            ok = not (e.code in (407, 502, 503, 504))
            detail = '' if ok else f'HTTP {e.code} from proxy'
        except Exception as e:  # noqa: BLE001
            ok, detail = False, raw(e)
        sites[i] = {'name': name, 'ok': ok, 'ms': int((time.time() - t0) * 1000) if ok else None,
                    'error': '' if ok else friendly(detail), 'detail': detail}

    threads = [threading.Thread(target=run, args=(i, n, u), daemon=True) for i, (n, u) in enumerate(TEST_SITES)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 2)
    sites = [s or {'name': n, 'ok': False, 'ms': None, 'error': tr('超时', 'timed out'), 'detail': 'timeout'}
             for s, (n, _) in zip(sites, TEST_SITES)]
    bad = [s for s in sites if not s['ok']]
    return {'proxy': p, 'type': 'SOCKS5' if route.scheme == 'socks5' else 'HTTP', 'address': route.hostport,
            'ok': not bad, 'sites': sites,
            'error': bad[0]['error'] if bad else '', 'detail': '; '.join(f"{s['name']}: {s['detail']}" for s in bad)}
