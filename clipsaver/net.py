"""How ClipSaver reaches the internet.

Proxy switch ON (default) - try, in order, until one works:
  1. the system proxy - what Clash / V2RayN / ShadowsocksX etc. set when their
     "system proxy" switch is on (Windows registry, macOS network settings or
     HTTP(S)_PROXY variables). This is what Chrome uses.
  2. proxies the user added in Settings.
  3. proxy apps found running on this computer (their usual local ports),
     covering apps set to PAC mode or with "system proxy" off.
  4. a direct connection - works on open networks, or when the proxy app runs
     in TUN / enhanced mode and captures all traffic by itself.
Proxy switch OFF - direct connection only.

Both HTTP and SOCKS5 proxies work. The route that worked is remembered in
memory (per server) and tried first next time. Nothing is written to disk.
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

# Local ports of common proxy apps (HTTP, SOCKS5 or both).
COMMON_PORTS = [
    (7890, 'Clash / ClashX / mihomo'),
    (7897, 'Clash Verge'),
    (7892, 'Clash 系客户端'),
    (7891, 'Clash SOCKS'),
    (10809, 'v2rayN'),
    (10808, 'v2rayN'),
    (1087, 'ShadowsocksX-NG / V2rayU'),
    (1086, 'ShadowsocksX-NG'),
    (1080, 'Shadowsocks'),
    (8889, 'Qv2ray'),
    (2080, 'NekoBox / sing-box'),
    (12334, 'Hiddify'),
    (6152, 'Surge'),
    (6153, 'Surge SOCKS'),
    (20171, 'v2rayA'),
    (20170, 'v2rayA SOCKS'),
]
TEST_URL = 'https://cdn.syndication.twimg.com/tweet-result?id=20&token=x'

_lock = threading.Lock()
_remembered = {}       # host -> route key that worked last time
_last_good = [None]    # route that worked most recently, any host
_kind_cache = {}       # (host, port) -> 'http' | 'socks5' | None


class NetError(Exception):
    """All routes failed; the message can be shown to the user."""


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


class _SocksHTTPConnection(http.client.HTTPConnection):
    socks = None

    def connect(self):
        self.sock = socks5_connect(*self.socks, self.host, self.port, self.timeout)


class _SocksHTTPSConnection(http.client.HTTPSConnection):
    socks = None

    def connect(self):
        raw = socks5_connect(*self.socks, self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def _socks_opener(host, port):
    class H(urllib.request.HTTPHandler):
        def http_open(self, req):
            conn = type('C', (_SocksHTTPConnection,), {'socks': (host, port)})
            return self.do_open(conn, req)

    class HS(urllib.request.HTTPSHandler):
        def https_open(self, req):
            conn = type('C', (_SocksHTTPSConnection,), {'socks': (host, port)})
            return self.do_open(conn, req, context=ssl.create_default_context())

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), H, HS)


# ---------------------------------------------------------------- routes

class Route:
    def __init__(self, kind, proxy=None, label=''):
        self.kind, self.proxy, self.label = kind, proxy, label   # proxy: scheme://host:port

    def key(self):
        return self.proxy or 'direct'

    @property
    def scheme(self):
        return urllib.parse.urlsplit(self.proxy).scheme if self.proxy else ''

    @property
    def hostport(self):
        return urllib.parse.urlsplit(self.proxy).netloc if self.proxy else ''

    def describe(self):
        if not self.proxy:
            return '直接连接'
        name = {'system': '系统代理', 'local': '本机代理', 'manual': '手动代理'}[self.kind]
        extra = f'，{self.label}' if self.label else ''
        return f'{name} {self.hostport}（{"SOCKS5" if self.scheme == "socks5" else "HTTP"}{extra}）'

    def opener(self):
        if not self.proxy:
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if self.scheme == 'socks5':
            h, p = self.hostport.rsplit(':', 1)
            return _socks_opener(h, int(p))
        return urllib.request.build_opener(urllib.request.ProxyHandler({'http': self.proxy, 'https': self.proxy}))


def parse_proxy(value):
    """'127.0.0.1:7890' / 'http://…' / 'socks5://…' -> normalised URL, or None."""
    if not value:
        return None
    value = value.strip()
    if '://' not in value:
        value = 'http://' + value
    u = urllib.parse.urlsplit(value)
    scheme = {'http': 'http', 'https': 'http', 'socks5': 'socks5', 'socks5h': 'socks5', 'socks': 'socks5'}.get(u.scheme.lower())
    if not scheme or not u.hostname or not u.port:
        return None
    return f'{scheme}://{u.hostname}:{u.port}'


def system_proxy():
    """The OS proxy, read the way Chrome does (PAC files are not read)."""
    try:
        p = urllib.request.getproxies()
    except Exception:  # noqa: BLE001
        return None
    for k in ('https', 'http', 'all', 'socks'):
        v = parse_proxy(p.get(k))
        if v:
            return v
    return None


def port_open(port, host='127.0.0.1', timeout=0.25):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def sniff(host, port):
    """Which protocol a local proxy port speaks: 'http', 'socks5' or None."""
    key = (host, port)
    if key in _kind_cache:
        return _kind_cache[key]
    kind = None
    try:   # HTTP proxies answer a CONNECT line with "HTTP/1.x ..."
        with socket.create_connection((host, port), timeout=0.6) as s:
            s.settimeout(0.8)
            s.sendall(b'CONNECT 127.0.0.1:9 HTTP/1.1\r\nHost: 127.0.0.1:9\r\n\r\n')
            if s.recv(5).startswith(b'HTTP/'):
                kind = 'http'
    except OSError:
        pass
    if not kind:
        try:   # SOCKS5 proxies answer the greeting with 05 00
            with socket.create_connection((host, port), timeout=0.6) as s:
                s.settimeout(0.8)
                s.sendall(b'\x05\x01\x00')
                if s.recv(2) == b'\x05\x00':
                    kind = 'socks5'
        except OSError:
            pass
    _kind_cache[key] = kind
    return kind


def fix_type(proxy):
    """If a typed proxy's port actually speaks the other protocol, use that one."""
    if not proxy:
        return proxy
    u = urllib.parse.urlsplit(proxy)
    real = sniff(u.hostname, u.port) if u.hostname in ('127.0.0.1', 'localhost') or u.hostname else None
    return f'{real}://{u.hostname}:{u.port}' if real and real != u.scheme else proxy


def local_proxies():
    found = []
    for port, name in COMMON_PORTS:
        if port_open(port):
            kind = sniff('127.0.0.1', port)
            if kind:
                found.append(Route('local', f'{kind}://127.0.0.1:{port}', name))
    return found


def routes(enabled=True, manual=()):
    if not enabled or os.environ.get('CLIPSAVER_ONLY_DIRECT'):
        return [Route('direct')]
    out = []
    sp = system_proxy()
    if sp:
        out.append(Route('system', sp))
    for m in manual or ():
        p = fix_type(parse_proxy(m))
        if p:
            out.append(Route('manual', p))
    out += local_proxies()
    out.append(Route('direct'))
    seen, uniq = set(), []
    for r in out:
        if r.key() not in seen:
            seen.add(r.key())
            uniq.append(r)
    return uniq


def _ordered(host, candidates):
    prefer = [_remembered.get(host), _last_good[0]]
    first = [c for k in prefer if k for c in candidates if c.key() == k]
    seen, out = set(), []
    for r in first + candidates:
        if r.key() not in seen:
            seen.add(r.key())
            out.append(r)
    return out


def _blocked_for_test(route):
    # Tests can pretend the direct route is blocked (like an unreachable site).
    return not route.proxy and os.environ.get('CLIPSAVER_BLOCK_DIRECT')


def _fresh(req):
    # A Request is changed by the proxy handler, so every attempt gets its own copy.
    return urllib.request.Request(req.full_url, data=req.data, headers=dict(req.header_items()),
                                  method=req.get_method())


def _good(host, route):
    with _lock:
        _remembered[host] = route.key()
        _last_good[0] = route.key()


def open_url(req, enabled=True, manual=(), timeout=20):
    """Open a request, trying routes in order. Returns (response, route).

    HTTP errors (404, 403…) come from the website itself, so they are raised at
    once. Network failures, and proxies that answer 407/502/503/504, move on to
    the next route.
    """
    host = urllib.parse.urlsplit(req.full_url).hostname or ''
    candidates = routes(enabled, manual)
    many = len(candidates) > 1
    tried = []
    for route in _ordered(host, candidates):
        try:
            if _blocked_for_test(route):
                raise urllib.error.URLError('blocked (test)')
            resp = route.opener().open(_fresh(req), timeout=min(timeout, 10) if many else timeout)
        except urllib.error.HTTPError as e:
            if route.proxy and e.code in (407, 502, 503, 504) and many:
                tried.append(route.describe())
                continue
            _good(host, route)
            raise
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, http.client.HTTPException):
            tried.append(route.describe())
            continue
        _good(host, route)
        return resp, route
    raise NetError(failure_message(enabled, tried))


def failure_message(enabled, tried):
    if not enabled:
        return ('直接连接连不上这个网站。如果你平时用代理软件（Clash、V2Ray、Shadowsocks 等），'
                '请在「设置 → 网络」里打开「代理」开关。')
    return ('试过这些线路都连不上：' + '、'.join(tried) + '。请确认代理软件已经打开；'
            '如果它是「规则」模式，可以试试切到「全局」模式，或者在「设置 → 网络」里新增它的端口。')


# ---------------------------------------------------------------- settings page

def check(route, timeout=6):
    """Can this route reach X right now? -> {'ok': bool, 'ms': int | None, 'error': str}"""
    req = urllib.request.Request(TEST_URL, headers={'User-Agent': 'Googlebot'})
    t0 = time.time()
    try:
        if _blocked_for_test(route):
            raise urllib.error.URLError('blocked (test)')
        with route.opener().open(req, timeout=timeout) as r:
            r.read(1)
    except urllib.error.HTTPError as e:
        if not (route.proxy and e.code in (407, 502, 503, 504)):
            return {'ok': True, 'ms': int((time.time() - t0) * 1000), 'error': ''}   # site answered
        return {'ok': False, 'ms': None, 'error': f'代理返回 {e.code}'}
    except Exception as e:  # noqa: BLE001
        return {'ok': False, 'ms': None, 'error': friendly(getattr(e, 'reason', e))}
    return {'ok': True, 'ms': int((time.time() - t0) * 1000), 'error': ''}


def friendly(err):
    """Short plain-Chinese reason for the Settings list."""
    s = str(err)
    low = s.lower()
    if isinstance(err, ConnectionRefusedError) or '10061' in s or 'refused' in low:
        return '这个端口没有程序在用，代理软件没开或端口不对'
    if isinstance(err, (TimeoutError, socket.timeout)) or 'timed out' in low:
        return '超时，连不上'
    if 'socks5 proxy refused' in low:
        return '不是 SOCKS5 端口，或需要密码'
    if 'socks5 proxy could not reach' in low or 'tunnel connection failed' in low:
        return '代理连不上 X，请检查代理软件的节点'
    if 'remote end closed' in low or 'badstatusline' in low:
        return '端口类型不对（HTTP 和 SOCKS5 填反了？）'
    if 'getaddrinfo' in low or 'name or service' in low or '11001' in s:
        return '网址解析失败（DNS）'
    if 'ssl' in low or 'eof' in low or 'reset' in low or '10054' in s:
        return '连接被中断（可能被拦截）'
    if 'blocked (test)' in low:
        return '连不上（测试模拟）'
    return s[:60]


def status(enabled=True, manual=()):
    """Every route with a live check, for the Settings list."""
    found = routes(True, manual)
    results = [None] * len(found)

    def run(i, r):
        results[i] = check(r)

    threads = [threading.Thread(target=run, args=(i, r), daemon=True) for i, r in enumerate(found)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(8)
    out = []
    for r, res in zip(found, results):
        res = res or {'ok': False, 'ms': None, 'error': '检测超时'}
        out.append({'kind': r.kind, 'proxy': r.proxy, 'address': r.hostport or '',
                    'type': ('SOCKS5' if r.scheme == 'socks5' else 'HTTP') if r.proxy else '',
                    'label': r.label, 'describe': r.describe(), **res})
    last = next((r.describe() for r in found if r.key() == _last_good[0]), None)
    return {'enabled': enabled, 'routes': out, 'last': last}
