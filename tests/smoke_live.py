"""Live end-to-end check against a running ClipSaver (real network, real downloads).

    python tests/smoke_live.py http://127.0.0.1:8790 <folder-the-app-saves-to>

Used in GitHub Actions on a real Mac against the packaged app.
"""
import json

import sys
import time
import urllib.request
from pathlib import Path

BASE = sys.argv[1].rstrip('/')
FOLDER = Path(sys.argv[2])
CASES = [
    # (link, expected sources)
    ('https://x.com/DavidToons_/status/1578353380363501568', ['main', 'quote']),
    ('https://x.com/primevideouk/status/1578401165338976258', ['card', 'card']),
    ('https://www.threads.com/@natgeo/post/DgI5S-xHSVS', ['quote'] * 4),
]


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=60) as r:
        return json.load(r)


def get(path):
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(BASE + path, timeout=30) as r:
        return json.load(r)


for _ in range(60):
    try:
        get('/api/ping')
        break
    except Exception:  # noqa: BLE001
        time.sleep(1)
else:
    sys.exit('app did not start')

assert b'ClipSaver' in urllib.request.build_opener(urllib.request.ProxyHandler({})).open(BASE + '/').read()
failures = 0
for link, want in CASES:
    r = post('/api/inspect', {'url': link})
    got = [v['source'] for v in r.get('videos', [])]
    if got != want:
        print('FAIL inspect', link, r.get('error'), got)
        failures += 1
        continue
    v = r['videos'][-1]
    s = {}
    job = post('/api/download', {'url': v['variants'][0]['url'], 'name': f"{r['site']}_{r['id']}", 'site': r['site']})['job']
    for _ in range(120):
        s = get('/api/job/' + job)
        if s['state'] != 'running':
            break
        time.sleep(0.5)
    if s['state'] != 'ok':
        print('FAIL download', link, s)
        failures += 1
        continue
    path = Path(s['path'])
    ok = path.parent == FOLDER and path.stat().st_size > 50_000 and path.read_bytes()[4:8] == b'ftyp'
    print('OK  ' if ok else 'FAIL', link, '->', path.name, path.stat().st_size, 'bytes')
    failures += 0 if ok else 1

print('failures:', failures)
sys.exit(1 if failures else 0)
