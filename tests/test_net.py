"""Offline tests of proxy parsing and route order. Run: python -m unittest -v"""
import unittest
from unittest import mock

from clipsaver import net


class ParseTest(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(net.parse_proxy('127.0.0.1:7890'), 'http://127.0.0.1:7890')
        self.assertEqual(net.parse_proxy('http://127.0.0.1:7890/'), 'http://127.0.0.1:7890')
        self.assertEqual(net.parse_proxy('socks5h://127.0.0.1:7891'), 'socks5://127.0.0.1:7891')
        self.assertEqual(net.parse_proxy(' SOCKS5://localhost:1080 '), 'socks5://localhost:1080')
        for bad in ('', 'abc', '127.0.0.1', 'ftp://1.2.3.4:21'):
            self.assertIsNone(net.parse_proxy(bad), bad)


class OrderTest(unittest.TestCase):
    def setUp(self):
        net._remembered.clear()
        net._last_good[0] = None

    def test_order_and_switch(self):
        local = [net.Route('local', 'http://127.0.0.1:7890', 'Clash')]
        with mock.patch.object(net, 'system_proxy', return_value='http://127.0.0.1:7890'), \
             mock.patch.object(net, 'local_proxies', return_value=local), \
             mock.patch.object(net, 'fix_type', side_effect=lambda p: p):
            rs = net.routes(True, ['socks5://127.0.0.1:1080'])
            self.assertEqual([r.kind for r in rs], ['system', 'manual', 'direct'])   # 7890 not repeated
            self.assertEqual([r.kind for r in net.routes(False, ['socks5://127.0.0.1:1080'])], ['direct'])

    def test_remembered_route_first(self):
        rs = [net.Route('system', 'http://a:1'), net.Route('direct')]
        net._remembered['x.com'] = 'direct'
        self.assertEqual(net._ordered('x.com', rs)[0].kind, 'direct')
        self.assertEqual(net._ordered('other.com', rs)[0].kind, 'system')


class FriendlyTest(unittest.TestCase):
    def test_messages(self):
        self.assertIn('没有程序', net.friendly(ConnectionRefusedError(10061, 'refused')))
        self.assertIn('超时', net.friendly(TimeoutError('timed out')))
        self.assertIn('填反', net.friendly(Exception('Remote end closed connection without response')))


if __name__ == '__main__':
    unittest.main()
