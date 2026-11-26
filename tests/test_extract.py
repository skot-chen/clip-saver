"""Offline tests on saved real responses. Run: python -m unittest -v"""
import json
import unittest
from pathlib import Path

from clipsaver import extract

FIX = Path(__file__).parent / 'fixtures'


def x(name):
    return extract.parse_x(json.loads((FIX / name).read_text(encoding='utf-8')))


def th(name, code='CODE'):
    return extract.parse_threads_embed((FIX / name).read_text(encoding='utf-8'), code)


def sources(result):
    return [(v['source'], v['author']) for v in result['videos']]


class DetectTest(unittest.TestCase):
    def test_links(self):
        cases = {
            'https://x.com/a/status/123?s=20': ('x', '123'),
            'https://twitter.com/a/status/123/video/1': ('x', '123'),
            'https://x.com/i/status/123': ('x', '123'),
            'https://mobile.twitter.com/a/statuses/123': ('x', '123'),
            'https://www.threads.com/@zuck/post/C9xRqbNPbx2?xmt=1': ('threads', 'C9xRqbNPbx2'),
            'https://www.threads.net/@a.b_c/post/DgI5S-xHSVS': ('threads', 'DgI5S-xHSVS'),
            'https://www.threads.com/t/DgI5S-xHSVS': ('threads', 'DgI5S-xHSVS'),
            '  https://www.threads.com/@a/post/Abc_-1  ': ('threads', 'Abc_-1'),
        }
        for url, want in cases.items():
            self.assertEqual(extract.detect(url), want, url)
        self.assertEqual(extract.detect('https://www.threads.com/share/xyz')[0], 'threads-share')
        self.assertIsNone(extract.detect('hello'))
        self.assertIsNone(extract.detect('https://x.com/a'))

    def test_x_token_matches_js(self):
        # value from JS: ((1578401165338976258/1e15)*Math.PI).toString(36) without 0 and '.'
        self.assertEqual(extract.x_token('1578401165338976258'), '3tqoys6uduo')


class XTest(unittest.TestCase):
    def test_four_videos_best_first(self):
        r = x('x_four.json')
        self.assertEqual(len(r['videos']), 4)
        self.assertTrue(all(s == 'main' for s, _ in sources(r)))
        for v in r['videos']:
            rates = [q['bitrate'] for q in v['variants']]
            self.assertEqual(rates, sorted(rates, reverse=True))
            self.assertTrue(all('.m3u8' not in q['url'] for q in v['variants']))
            self.assertEqual((v['width'], v['height']), (v['variants'][0]['width'], v['variants'][0]['height']))

    def test_main_and_quote_are_labelled(self):
        r = x('x_main_quote.json')
        self.assertEqual(sources(r), [('main', 'DavidToons_'), ('quote', 'DiscussingFilm')])

    def test_card_videos_found(self):
        r = x('x_card.json')
        self.assertEqual([s for s, _ in sources(r)], ['card', 'card'])
        self.assertEqual(len({v['variants'][0]['url'] for v in r['videos']}), 2)

    def test_deleted_tweet(self):
        with self.assertRaises(extract.ExtractError):
            x('x_gone.json')


class ThreadsTest(unittest.TestCase):
    def test_quoted_single_video(self):
        r = th('th_quote_one.html')
        self.assertEqual(r['author'], 'zuck')
        self.assertEqual(sources(r), [('quote', 'rowancheung')])

    def test_carousel(self):
        r = th('th_carousel.html')
        self.assertEqual(sources(r), [('main', 'natgeoanimals')] * 4)
        urls = [v['variants'][0]['url'] for v in r['videos']]
        self.assertEqual(len(set(urls)), 4)
        self.assertTrue(all(u.startswith('https://') and '&amp;' not in u for u in urls))

    def test_quoted_carousel(self):
        r = th('th_quote_carousel.html')
        self.assertEqual(r['author'], 'natgeo')
        self.assertEqual(sources(r), [('quote', 'natgeoanimals')] * 4)

    def test_single(self):
        self.assertEqual(sources(th('th_single.html')), [('main', 'nba')])

    def test_photo_only(self):
        self.assertEqual(th('th_photo.html')['videos'], [])


if __name__ == '__main__':
    unittest.main()
