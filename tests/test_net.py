"""Offline tests of proxy parsing, route order and messages. Run: python -m unittest -v"""
import unittest
from unittest import mock

from clipsaver import net
from clipsaver.i18n import set_lang


class ParseTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(net.parse_proxy('127.0.0.1:7890'), 'http://127.0.0.1:7890')
        self.assertEqual(net.parse_proxy('http://127.0.0.1:7890/'), 'http://127.0.0.1:7890')
        self.assertEqual(net.parse_proxy('socks5h://127.0.0.1:7891'), 'socks5://127.0.0.1:7891')
        self.assertEqual(net.parse_proxy(' SOCKS5://localhost:1080 '), 'socks5://localhost:1080')
        for bad in ('', 'abc', '127.0.0.1', 'ftp://1.2.3.4:21', '127.0.0.1:99999'):
            self.assertIsNone(net.parse_proxy(bad), bad)


class RoutesTest(unittest.TestCase):
    def test_switch_and_order(self):
        with mock.patch.object(net, 'known_type', side_effect=lambda p: p):
            on = net.routes(True, ['127.0.0.1:7892', 'socks5://127.0.0.1:7890', '127.0.0.1:7892'])
            self.assertEqual([r.proxy for r in on],
                             ['http://127.0.0.1:7892', 'socks5://127.0.0.1:7890', None])   # list order, no repeats, direct last
            self.assertEqual([r.proxy for r in net.routes(False, ['127.0.0.1:7892'])], [None])


class MessageTest(unittest.TestCase):
    def tearDown(self):
        set_lang('zh')

    def test_friendly(self):
        self.assertIn('没有程序', net.friendly('ConnectionRefusedError: [Errno 61] Connection refused'))
        self.assertIn('超时', net.friendly('TimeoutError: timed out'))
        self.assertIn('证书', net.friendly('SSLCertVerificationError: certificate verify failed'))
        self.assertIn('填反', net.friendly('RemoteDisconnected: Remote end closed connection without response')
                      .replace('端口类型不对', '填反'))
        set_lang('en')
        self.assertEqual(net.friendly('TimeoutError: timed out'), 'timed out')

    def test_certificates_bundled_when_available(self):
        ctx = net.ssl_context()
        self.assertTrue(ctx.check_hostname)


if __name__ == '__main__':
    unittest.main()
