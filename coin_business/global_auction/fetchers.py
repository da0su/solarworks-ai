"""
coin_business/global_auction/fetchers.py
==========================================
世界オークション fetcher 実装。

現状はスタブ実装 (fetch_events / fetch_lots は空リストを返す)。
Day 8 以降でオークションハウスごとのスクレイピング / API 連携を追加する。

オークションハウス別実装状況:
  HeritageFetcher  : スタブ (Heritage Auctions)
  StacksFetcher    : スタブ (Stack's Bowers Galleries)
  SpinkFetcher     : スタブ (Spink & Son)
  NobleFetcher     : スタブ (Noble Numismatics)

Note: fetch_events() / fetch_lots() がスタブでも、
      sync / ingest スクリプトの全フロー・DB 操作・T-minus 計算は
      テスト可能。
"""

from __future__ import annotations

import logging

from global_auction.fetcher_base import AuctionFetcher

logger = logging.getLogger(__name__)


# ================================================================
# Heritage Auctions
# ================================================================

class HeritageFetcher(AuctionFetcher):
    """Heritage Auctions fetcher。

    公式サイト: https://coins.ha.com/
    サポートするカテゴリ: World Coins / US Coins
    """

    @property
    def auction_house(self) -> str:
        return "heritage"

    def fetch_events(self) -> list[dict]:
        """Heritage の公開オークション一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Heritage] fetch_events (stub)")
        return []

    def fetch_lots(self, event: dict) -> list[dict]:
        """Heritage オークションの lot 一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Heritage] fetch_lots event=%s (stub)",
                     event.get("event_id_external", "?"))
        return []


# ================================================================
# Stack's Bowers Galleries
# ================================================================

class StacksFetcher(AuctionFetcher):
    """Stack's Bowers Galleries fetcher。

    公式サイト: https://www.stacksbowers.com/
    """

    @property
    def auction_house(self) -> str:
        return "stacks_bowers"

    def fetch_events(self) -> list[dict]:
        """Stack's Bowers の公開オークション一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Stacks] fetch_events (stub)")
        return []

    def fetch_lots(self, event: dict) -> list[dict]:
        """Stack's Bowers オークションの lot 一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Stacks] fetch_lots event=%s (stub)",
                     event.get("event_id_external", "?"))
        return []


# ================================================================
# Spink & Son
# ================================================================

class SpinkFetcher(AuctionFetcher):
    """Spink & Son fetcher。

    公式サイト: https://www.spink.com/
    """

    @property
    def auction_house(self) -> str:
        return "spink"

    def fetch_events(self) -> list[dict]:
        """Spink の公開オークション一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Spink] fetch_events (stub)")
        return []

    def fetch_lots(self, event: dict) -> list[dict]:
        """Spink オークションの lot 一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Spink] fetch_lots event=%s (stub)",
                     event.get("event_id_external", "?"))
        return []


# ================================================================
# Noble Numismatics
# ================================================================

class NobleFetcher(AuctionFetcher):
    """Noble Numismatics fetcher。

    公式サイト: https://www.noble.com.au/
    """

    @property
    def auction_house(self) -> str:
        return "noble"

    def fetch_events(self) -> list[dict]:
        """Noble の公開オークション一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Noble] fetch_events (stub)")
        return []

    def fetch_lots(self, event: dict) -> list[dict]:
        """Noble オークションの lot 一覧を取得する。
        現状スタブ: 空リストを返す。
        """
        logger.debug("[Noble] fetch_lots event=%s (stub)",
                     event.get("event_id_external", "?"))
        return []


# ================================================================
# 全 fetcher 一覧
# ================================================================

ALL_FETCHERS: list[AuctionFetcher] = [
    HeritageFetcher(),
    StacksFetcher(),
    SpinkFetcher(),
    NobleFetcher(),
]

FETCHER_MAP: dict[str, AuctionFetcher] = {
    f.auction_house: f for f in ALL_FETCHERS
}
