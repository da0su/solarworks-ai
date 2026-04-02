"""
coin_business/global_auction/fetcher_base.py
=============================================
世界オークション fetcher の基底クラス。

各オークションハウスの fetcher はこのクラスを継承し、
fetch_events() と fetch_lots() を実装する。

設計方針:
  - fetch_events() は公開イベント情報を返す (スクレイピング or API)
  - fetch_lots()   は特定イベントの lot 一覧を返す
  - 両メソッドとも失敗時は [] / {} を返す (例外を外に伝播させない)
  - 返却 dict のキーは DB カラム名と一致させる
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AuctionFetcher(ABC):
    """
    世界オークション fetcher の基底クラス。

    サブクラス:
      HeritageFetcher   - Heritage Auctions
      StacksFetcher     - Stack's Bowers Galleries
      SpinkFetcher      - Spink & Son
      NobleFetcher      - Noble Numismatics
    """

    @property
    @abstractmethod
    def auction_house(self) -> str:
        """constants.Source に対応する auction_house 識別子。"""

    @abstractmethod
    def fetch_events(self) -> list[dict]:
        """
        公開イベントの一覧を取得して返す。

        Returns:
            list of EventDict:
              auction_house      (str)  : 必須
              event_name         (str)  : 必須
              event_id_external  (str)  : 必須 (ハウス固有 ID)
              event_url          (str)
              auction_date       (str)  : "YYYY-MM-DD"
              auction_start_at   (str)  : ISO8601
              auction_end_at     (str)  : ISO8601
              is_online          (bool) : デフォルト True
              coin_lot_count     (int)
              total_lot_count    (int)
              status             (str)  : "upcoming" | "active" | "ended"
        """

    @abstractmethod
    def fetch_lots(self, event: dict) -> list[dict]:
        """
        event の lot 一覧を取得して返す。

        Args:
            event: global_auction_events レコード (id / event_id_external を含む)

        Returns:
            list of LotDict:
              lot_id_external  (str)  : 必須 (ハウス固有 ID)
              lot_number       (str)
              lot_title        (str)  : 必須
              year             (int)
              country          (str)
              denomination     (str)
              grade_text       (str)  : 元の grade 表記
              grader           (str)  : 'NGC' | 'PCGS' | 'RAW'
              cert_company     (str)
              cert_number      (str)
              estimate_low_usd  (float)
              estimate_high_usd (float)
              current_bid_usd  (float)
              currency         (str)  : デフォルト 'USD'
              lot_url          (str)
              image_url        (str)
              lot_end_at       (str)  : ISO8601
              status           (str)  : "active" | "sold" | "passed"
        """
