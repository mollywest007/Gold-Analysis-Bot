"""Regression tests for gold-news relevance and freshness filtering."""
import email.utils
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import news


class NewsTests(unittest.TestCase):
    def test_rejects_unrelated_headlines(self):
        self.assertTrue(news._is_gold_relevant("Gold price rises as dollar weakens"))
        self.assertTrue(news._is_gold_relevant("XAU/USD outlook before CPI"))
        self.assertFalse(news._is_gold_relevant("Amazon stock rises after earnings"))
        self.assertFalse(news._is_gold_relevant("Goldman Sachs raises its stock target"))

    def test_rejects_untrusted_commentary_source(self):
        self.assertTrue(news._is_trusted_source("FXStreet"))
        self.assertTrue(news._is_trusted_source("Reuters"))
        self.assertFalse(news._is_trusted_source("TradingView"))
        self.assertFalse(news._is_trusted_source("Bitcoin World"))

    def test_rejects_gold_chart_pages_as_news(self):
        self.assertTrue(news._is_news_story("Gold rises as dollar weakens"))
        self.assertFalse(news._is_news_story("Gold Futures Streaming Chart"))
        self.assertFalse(news._is_news_story("XAU/USD Weekly Analysis"))

    def test_rss_parser_keeps_gold_headline_and_link(self):
        pubdate = email.utils.formatdate(time.time(), usegmt=True)
        xml = f"""<rss><channel><item>
          <title>Gold price rises on safe-haven demand</title>
          <link>https://news.google.com/example</link>
          <pubDate>{pubdate}</pubDate>
          <source>FXStreet</source>
        </item><item>
          <title>Unrelated technology stock update</title>
          <link>https://news.google.com/other</link>
          <pubDate>{pubdate}</pubDate>
          <source>Other News</source>
        </item></channel></rss>"""
        items = news._parse_rss(xml)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["source"], "FXStreet")
        self.assertEqual(items[0]["url"], "https://news.google.com/example")

    def test_old_headlines_are_filtered(self):
        old = email.utils.formatdate(time.time() - news.NEWS_MAX_AGE - 60, usegmt=True)
        xml = f"""<rss><channel><item>
          <title>Gold price historical recap</title>
          <link>https://news.google.com/old</link>
          <pubDate>{old}</pubDate>
          <source>FXStreet</source>
        </item></channel></rss>"""
        items = news._parse_rss(xml)
        self.assertEqual(len(items), 1)
        self.assertLess(items[0]["published_at"], time.time() - news.NEWS_MAX_AGE)


if __name__ == "__main__":
    unittest.main()