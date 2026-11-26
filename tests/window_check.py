"""Start the packaged app normally (with its window) and check that:
  1. the app window really opened (not the browser fallback),
  2. Quit closes the window and the program exits.

    python tests/window_check.py <path-to-executable>
"""
import json
import subprocess
import sys
import time
import urllib.request

EXE = sys.argv[1]
BASE = 'http://127.0.0.1:8790'
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path):
    req = urllib.request.Request(BASE + path, data=b'{}', headers={'Content-Type': 'application/json'})
    with opener.open(req, timeout=10) as r:
        return json.load(r)


proc = subprocess.Popen([EXE])
try:
    for _ in range(60):
        try:
            with opener.open(BASE + '/api/ping', timeout=2):
                break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    else:
        sys.exit('FAIL: app did not start')
    time.sleep(3)  # let the window finish opening
    shown = post('/api/show')
    print('window opened:', shown.get('window'))
    if not shown.get('window'):
        sys.exit('FAIL: no app window (fell back to browser)')
    post('/api/quit')
    proc.wait(timeout=20)
    print('OK  window closed and app exited, code', proc.returncode)
finally:
    if proc.poll() is None:
        proc.kill()
        sys.exit('FAIL: app still running after Quit')
