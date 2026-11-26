"""Find every downloadable video in an X or Threads post.

Only the Python standard library is used. Each video is labelled with where
it comes from, so posts that mix several sources are not confusing:
  main  - attached to the post itself
  quote - inside a post that this post quotes
  card  - inside a card (ads / promoted media) attached to the post
"""
import collections
import html
import json
import math
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from . import net
from .i18n import tr

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')


class ExtractError(Exception):
    """Error with a message that can be shown to the user as-is (+ raw detail)."""

    def __init__(self, message, detail=''):
        super().__init__(message)
        self.detail = detail


# ---------------------------------------------------------------- network

class Net:
    """Proxy switch on/off + the user's proxy list (✓ one first). See net.py."""

    def __init__(self, enabled=False, proxies=()):
        self.enabled, self.proxies, self.used = enabled, list(proxies or ()), None


def fetch(netcfg, url, headers=None, timeout=8, body=True):
    """GET a page. body=False only follows redirects and returns the final address
    (share links: no need to download the 600 KB page behind them)."""
    req = urllib.request.Request(url, headers={'User-Agent': UA, **(headers or {})})
    try:
        resp, route = net.open_url(req, netcfg.enabled, netcfg.proxies, timeout)
        netcfg.used = route.describe()
        with resp as r:
            return r.geturl(), (r.read() if body else b'')
    except urllib.error.HTTPError as e:
        raise ExtractError(tr(f'网站返回错误 {e.code}（链接可能失效或帖子不公开）',
                              f'The site returned error {e.code} (the link may be broken or the post private)'),
                           f'HTTP {e.code} {url}') from e
    except net.NetError as e:
        raise ExtractError(str(e), e.detail) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ExtractError(tr('网络中断了，请再试一次。', 'The connection dropped. Please try again.'), net.raw(e)) from e


# ---------------------------------------------------------------- url parsing

X_RE = re.compile(r'(?:twitter\.com|x\.com)/(?:[^/?#]+|i(?:/web)?)/status(?:es)?/(\d+)', re.I)
THREADS_RE = re.compile(r'threads\.(?:net|com)/(?:@[^/?#]+/post|t|post)/([A-Za-z0-9_-]+)', re.I)
THREADS_SHARE_RE = re.compile(r'threads\.(?:net|com)/share/', re.I)


def detect(url):
    """Return ('x', status_id) / ('threads', code) / ('threads-share', url) / None."""
    url = (url or '').strip()
    m = X_RE.search(url)
    if m:
        return 'x', m.group(1)
    m = THREADS_RE.search(url)
    if m:
        return 'threads', m.group(1)
    if THREADS_SHARE_RE.search(url):
        return 'threads-share', url
    return None


# ---------------------------------------------------------------- X

def _js_number_to_string(val, radix):
    # Port of JS Number.prototype.toString(radix) (from yt-dlp, public domain).
    alphabet = b'0123456789abcdefghijklmnopqrstuvwxyz.-'
    result = collections.deque()
    fraction, integer = math.modf(val)
    delta = max(math.nextafter(.0, math.inf), math.ulp(val) / 2)
    if fraction >= delta:
        result.append(-2)
    while fraction >= delta:
        delta *= radix
        fraction, digit = math.modf(fraction * radix)
        result.append(int(digit))
        needs_rounding = fraction > 0.5 or (fraction == 0.5 and int(digit) & 1)
        if needs_rounding and fraction + delta > 1:
            for index in reversed(range(1, len(result))):
                if result[index] + 1 < radix:
                    result[index] += 1
                    break
                result.pop()
            else:
                integer += 1
            break
    integer, digit = divmod(int(integer), radix)
    result.appendleft(digit)
    while integer > 0:
        integer, digit = divmod(integer, radix)
        result.appendleft(digit)
    return bytes(alphabet[d] for d in result).decode('ascii')


def x_token(status_id):
    s = _js_number_to_string((int(status_id) / 1e15) * math.pi, 36)
    return s.replace('0', '').replace('.', '')


def _x_video(media, source, author):
    info = media.get('video_info') or {}
    variants = []
    for v in info.get('variants') or []:
        if v.get('content_type') != 'video/mp4' or not v.get('url'):
            continue
        m = re.search(r'/(\d+)x(\d+)/', v['url'])
        variants.append({
            'url': v['url'],
            'bitrate': v.get('bitrate') or 0,
            'width': int(m.group(1)) if m else None,
            'height': int(m.group(2)) if m else None,
        })
    if not variants:
        return None
    variants.sort(key=lambda v: (v['bitrate'], v['width'] or 0), reverse=True)
    size = media.get('original_info') or {}
    return {
        'id': str(media.get('id_str') or ''),
        'source': source,
        'author': author,
        'kind': 'gif' if media.get('type') == 'animated_gif' else 'video',
        'duration': (info.get('duration_millis') or 0) / 1000 or None,
        # Size of the best file we can actually download, not the upload size.
        'width': variants[0]['width'] or size.get('width'),
        'height': variants[0]['height'] or size.get('height'),
        'thumb': media.get('media_url_https'),
        'variants': variants,
    }


def parse_x(data):
    """Turn a syndication tweet-result JSON into our result shape."""
    if not data or data.get('__typename') == 'TweetTombstone':
        raise ExtractError(tr('这条推文看不到（已删除、受保护，或需要登录才能看）', 'This post cannot be seen (deleted, protected, or login required).'))
    author = (data.get('user') or {}).get('screen_name') or ''
    videos, seen, photos = [], set(), 0

    def add(media_list, source, who):
        nonlocal photos
        for media in media_list or []:
            if media.get('type') == 'photo':
                photos += 1
                continue
            v = _x_video(media, source, who)
            if v and v['variants'][0]['url'] not in seen:
                seen.add(v['variants'][0]['url'])
                videos.append(v)

    add(data.get('mediaDetails'), 'main', author)
    card = data.get('card') or {}
    uc = ((card.get('binding_values') or {}).get('unified_card') or {}).get('string_value')
    if uc:
        try:
            add((json.loads(uc).get('media_entities') or {}).values(), 'card', author)
        except ValueError:
            pass
    quote = data.get('quoted_tweet') or {}
    add(quote.get('mediaDetails'), 'quote', (quote.get('user') or {}).get('screen_name') or '')
    return {
        'site': 'x',
        'id': data.get('id_str'),
        'author': author,
        'text': data.get('text') or '',
        'photos': photos,
        'videos': videos,
    }


def extract_x(status_id, opener):
    url = ('https://cdn.syndication.twimg.com/tweet-result?'
           + urllib.parse.urlencode({'id': status_id, 'token': x_token(status_id)}))
    _, body = fetch(opener, url, headers={'User-Agent': 'Googlebot'})
    try:
        data = json.loads(body or b'{}')
    except ValueError as e:
        raise ExtractError(tr('X 返回的数据看不懂，稍后再试', 'X sent data ClipSaver cannot read. Try again later.')) from e
    result = parse_x(data)
    result['id'] = result['id'] or status_id
    return result


# ---------------------------------------------------------------- Threads

def parse_threads_embed(page, code):
    """Parse https://www.threads.com/t/<code>/embed HTML."""
    authors = [(m.start(), html.unescape(m.group(1))) for m in re.finditer(
        r'class="HeaderLink"><span>([^<]+)</span>', page)]
    main_author = authors[0][1] if authors else ''
    # Everything after the first quote-post marker belongs to the quoted post.
    qm = re.search(r'class="[^"]*QuotePost', page)
    quote_start = qm.start() if qm else len(page)
    quote_author = next((a for pos, a in authors if pos > quote_start), '')

    videos, seen = [], set()
    for m in re.finditer(r'<video\b[^>]*>(.*?)</video>', page, re.S):
        src = re.search(r'<source[^>]+src="([^"]+)"', m.group(1)) or re.search(r'\bsrc="([^"]+)"', m.group(0))
        if not src:
            continue
        url = html.unescape(src.group(1))
        if url.startswith('blob:') or url in seen:
            continue
        seen.add(url)
        poster = re.search(r'poster="([^"]+)"', m.group(0))
        is_quote = m.start() >= quote_start
        videos.append({
            'id': f'{code}_{len(videos) + 1}',
            'source': 'quote' if is_quote else 'main',
            'author': quote_author if is_quote else main_author,
            'kind': 'video',
            'duration': None, 'width': None, 'height': None,
            'thumb': html.unescape(poster.group(1)) if poster else None,
            'variants': [{'url': url, 'bitrate': 0, 'width': None, 'height': None}],
        })
    text = re.search(r'class="[^"]*TextContentContainer[^"]*"[^>]*>(.*?)</span>', page, re.S)
    text = re.sub(r'<[^>]+>', '', html.unescape(text.group(1))).strip() if text else ''
    photos = len(re.findall(r'class="[^"]*(?:MediaScrollImageContainer|SingleInnerMediaContainerImage)', page))
    return {'site': 'threads', 'id': code, 'author': main_author, 'text': text,
            'photos': photos, 'videos': videos}


# Set by the app when it runs with its own window: reads a post the way a
# browser does (some videos, e.g. ones shared over from Instagram, are only
# loaded by Threads' own scripts and are missing from the embed page).
# Signature: browser_reader(page_url, code, cancel: threading.Event) -> [{'src', 'poster', 'w', 'h', 'd'}]
browser_reader = None


def videos_from_browser(found, code, author):
    videos, seen = [], set()
    for v in found:
        src = v.get('src') or ''
        if not src.startswith('https://') or src in seen:
            continue
        seen.add(src)
        videos.append({
            'id': f'{code}_{len(videos) + 1}', 'source': 'main', 'author': author, 'kind': 'video',
            'duration': v.get('d') or None, 'width': v.get('w') or None, 'height': v.get('h') or None,
            'thumb': v.get('poster') or None,
            'variants': [{'url': src, 'bitrate': 0, 'width': v.get('w') or None, 'height': v.get('h') or None}],
        })
    return videos


def extract_threads(code, opener):
    # The quick way (embed page) and the browser way run at the same time; the
    # browser one is stopped as soon as the quick way has found the videos.
    reader, cancel, box = None, threading.Event(), {}
    if browser_reader:
        def run():
            try:
                box['found'] = browser_reader(f'https://www.threads.com/t/{code}', code, cancel)
            except Exception as e:  # noqa: BLE001
                box['error'] = e
        reader = threading.Thread(target=run, daemon=True)
        reader.start()
    try:
        # A browser User-Agent gets the JS app; a plain one gets server-rendered HTML.
        _, body = fetch(opener, f"https://www.threads.com/t/{code}/embed",
                        headers={"User-Agent": "ClipSaver/1.0"})
        page = body.decode('utf-8', 'replace')
        if 'HeaderLink' not in page and '<video' not in page:
            raise ExtractError(tr('这条 Threads 帖子看不到（可能已删除或不公开）', 'This Threads post cannot be seen (deleted or not public).'))
        result = parse_threads_embed(page, code)
    except ExtractError:
        if not reader:
            raise
        reader.join(13)                      # the quick way failed: the browser way may still work (it gives up after 12 s)
        if not box.get('found'):
            raise
        result = {'site': 'threads', 'id': code, 'author': '', 'text': '', 'photos': 0, 'videos': []}
    if result['videos']:
        cancel.set()
    elif reader:
        reader.join(13)
        result['videos'] = videos_from_browser(box.get('found') or [], code, result['author'])
        result['via_browser'] = bool(result['videos'])
    return result


def resolve_share(url, opener):
    final, _ = fetch(opener, url, headers={"User-Agent": "ClipSaver/1.0"}, body=False)
    m = THREADS_RE.search(final)
    if not m:
        raise ExtractError(tr('这个分享链接没有跳转到具体帖子', 'This share link does not lead to a post.'))
    return m.group(1)


# ---------------------------------------------------------------- entry

def inspect(url, enabled=False, proxies=()):
    opener = Net(enabled, proxies)
    kind = detect(url)
    if not kind:
        raise ExtractError(tr('认不出这个链接。请粘贴 X（x.com / twitter.com）或 Threads 的帖子链接。',
                              'That link is not recognised. Paste an X (x.com / twitter.com) or Threads post link.'))
    site, value = kind
    if site == 'x':
        result = extract_x(value, opener)
    else:
        code = resolve_share(value, opener) if site == 'threads-share' else value
        result = extract_threads(code, opener)
    if not result['videos']:
        extra = tr(f'（有 {result["photos"]} 张图片）', f' ({result["photos"]} photos)') if result['photos'] else ''
        if site != 'x' and not browser_reader:
            raise ExtractError(tr('快速方式没找到视频。有些 Threads 视频（比如从 Instagram 分享过来的）'
                                  '要用 ClipSaver 的窗口版才能读取，请从「应用程序」里打开 ClipSaver 再试。',
                                  'No video found the quick way. Some Threads videos (e.g. shared from Instagram) '
                                  'can only be read by the ClipSaver app window — open ClipSaver from Applications and try again.'))
        raise ExtractError(tr(f'这条帖子里没有能下载的视频{extra}。直播、外链视频（比如 YouTube）下载不了。',
                              f'This post has no downloadable video{extra}. Live streams and links to other sites '
                              '(e.g. YouTube) cannot be downloaded.'))
    result['route'] = opener.used
    return result
