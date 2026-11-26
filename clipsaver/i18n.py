"""Chinese / English for messages made on the Python side (errors etc.).

The page sends its language with every request; the server sets it for the
thread that handles the request, and passes it on to download threads.
"""
import threading

_local = threading.local()


def set_lang(lang):
    _local.lang = 'en' if lang == 'en' else 'zh'


def get_lang():
    return getattr(_local, 'lang', 'zh')


def tr(zh, en):
    return en if get_lang() == 'en' else zh
