# CAP実行順序 — 詳細実装ガイド

> **このドキュメントは cap_master_plan_10_phases.md の実装詳細版。**
> マスタープランの「何を」「なぜ」はそちら参照。ここは「どう作るか」。

---

## 固定ルール（コード定数に反映すること）

| # | ルール | 値 |
|---|--------|-----|
| 0-1 | 母集団 | Yahoo!落札履歴DB のみ |
| 0-2 | Level A 定義 | cert完全一致 OR Yahoo基準より高グレード+利益達成 OR ±5年以内+利益達成 |
| 0-3 | BOT抽出 → CAP監査必須 | AUDIT_PASS のみ daily_candidates へ |
| 0-4 | eBay API | 正式 API 主ルート（承認取得済み） |
| 0-5 | BitNow | NEGOTIATE_LATER 保存のみ。自動入札・自動交渉なし |
| 0-6 | Yahoo!履歴 | 最初の10日間は CEO 承認必須。その後 CAP 監査主体へ |
| 0-7 | Slack / Notion / Dashboard | 役割を分離。通知=Slack、台帳=Notion、判断UI=Dashboard |
| 0-8 | 世界オークション | T-21/T-7/T-3/T-1 の T-minus 運用 |

---

## フェーズ別 実装仕様

### Phase 1 — ルール凍結

**担当**: COO / CAP Backend
**期間**: Day18〜Day19（1-2日）

#### やること

```python
# coin_business/scripts/constants.py  (新規 or 既存に追記)

# 候補レベル定義
CANDIDATE_LEVELS = {
    "A": "cert完全一致 OR 高グレード+利益達成 OR ±5年以内+利益達成",
    "B": "価格参考のみ。候補化しない",
    "C": "除外",
}

# KEEP 監視頻度（秒）
KEEP_WATCH_INTERVALS = {
    "normal":  3 * 3600,   # 通常: 3時間
    "24h":     1 * 3600,   # 24時間以内: 1時間
    "6h":      30 * 60,    # 6時間以内: 30分
    "1h":      10 * 60,    # 1時間以内: 10分
}

# BitNow 扱い
BITNOW_POLICY = "NEGOTIATE_LATER"

# Yahoo!履歴: CEO 承認期間
YAHOO_CEO_APPROVAL_DAYS = 10
```

**完了条件**: 上記定数がコードに存在し、doc に反映されている

---

### Phase 2 — Yahoo!履歴 staging 実装

**担当**: CAP Backend
**期間**: Day19〜Day20（1-2日）

#### 新テーブル

- `yahoo_sold_lots_staging` → `migrations/012_yahoo_staging.sql`
- `yahoo_sold_lot_reviews` → 同上

#### 新スクリプト

**`coin_business/scripts/yahoo_sold_sync.py`**

```python
"""
Yahoo!落札履歴を staging に保存するジョブ。
本DBには一切書かない。
"""

def sync_yahoo_lots_to_staging(days_back: int = 7) -> dict:
    """
    yahoo_sold_lots (既存) から新規レコードを取得し
    yahoo_sold_lots_staging に PENDING_CEO で保存。
    重複は skip。
    Returns: {"inserted": N, "skipped": N, "errors": N}
    """
```

**処理フロー**:
```
yahoo_sold_lots (既存DB) → staging テーブル → status=PENDING_CEO
※ 本DBの yahoo_sold_lots を直接変更しない
```

**完了条件**: Yahoo!新規履歴が staging に蓄積。本DB汚れなし。

---

### Phase 3 — Yahoo!履歴 CEO確認待ちタブ

**担当**: CAP UI / CAP Backend
**期間**: Day20〜Day21（1-2日）

#### dashboard.py 追加関数

```python
def get_yahoo_pending_reviews(limit: int = 100) -> list[dict]:
    """
    yahoo_sold_lots_staging から status='PENDING_CEO' を取得。
    created_at DESC 順。
    """

def save_yahoo_staging_review(
    staging_id: str,
    decision: str,          # 'approved' | 'rejected' | 'held'
    reason: str | None = None,
    reviewer: str = "ceo",
) -> bool:
    """
    yahoo_sold_lot_reviews に保存。
    approved の場合は staging の status を APPROVED_TO_MAIN に更新。
    """
```

#### dashboard.py タブ追加 — `render_yahoo_pending_review_tab()`

```python
def render_yahoo_pending_review_tab():
    st.header("📚 Yahoo!履歴 確認待ち")

    items = get_yahoo_pending_reviews(limit=200)
    if not items:
        st.success("確認待ちゼロ件")
        return

    st.info(f"確認待ち: {len(items)}件")

    for item in items:
        with st.expander(f"{item['lot_title']} — {item['sold_date']}"):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.write(f"**落札価格**: ¥{item.get('sold_price_jpy', 0):,}")
                st.write(f"**cert**: {item.get('cert_company','')} {item.get('cert_number','')}")
                st.write(f"**grade**: {item.get('grade','')}")
                if item.get('source_url'):
                    st.markdown(f"[📎 ソース]({item['source_url']})")
            with col2:
                sid = str(item['id'])
                if st.button("✅ 承認", key=f"yap_{sid}"):
                    save_yahoo_staging_review(sid, "approved")
                    st.rerun()
                if st.button("⏸ 保留", key=f"yph_{sid}"):
                    save_yahoo_staging_review(sid, "held")
                    st.rerun()
                if st.button("❌ 却下", key=f"yrj_{sid}"):
                    save_yahoo_staging_review(sid, "rejected")
                    st.rerun()
```

**完了条件**: CEO が staging をレビューできる。approved/rejected/held が保存される。

---

### Phase 4 — 承認済みYahoo!履歴の昇格と seed 生成

**担当**: CAP Backend
**期間**: Day21〜Day22（1-2日）

#### 新テーブル

- `yahoo_coin_seeds` → `migrations/013_yahoo_seeds.sql`

#### 新スクリプト

**`coin_business/scripts/yahoo_promoter.py`**

```python
"""
yahoo_sold_lots_staging の APPROVED_TO_MAIN を
yahoo_sold_lots へ昇格させるジョブ。
"""

def promote_approved_yahoo_lots() -> dict:
    """
    1. staging の status='APPROVED_TO_MAIN' を取得
    2. yahoo_sold_lots に upsert (重複skip)
    3. staging の status を PROMOTED に更新
    Returns: {"promoted": N, "skipped": N}
    """
```

**`coin_business/scripts/seed_generator.py`**

```python
"""
yahoo_sold_lots (本DB) から yahoo_coin_seeds を生成。
staging データは絶対に使わない。
"""

SEED_TYPES = [
    "cert_exact",      # cert company + number 完全一致
    "title_fuzzy",     # タイトル類似
    "year_grade",      # 年号±5年 + グレード
]

def generate_seeds_from_yahoo_lots() -> dict:
    """
    yahoo_sold_lots を走査してシード生成。
    Returns: {"created": N, "updated": N, "skipped": N}
    """
```

**完了条件**: 未承認履歴は seed に混ざらない。承認済みだけ探索対象になる。

---

### Phase 5 — eBay API 正式連携と全域監視

**担当**: CAP Backend / Ops
**期間**: Day22〜Day26（3-5日）

#### 新テーブル

- `ebay_listings_raw` → `migrations/014_ebay_listing_tables.sql`
- `ebay_listing_snapshots` → 同上
- `ebay_seed_hits` → 同上

#### 新スクリプト

**`coin_business/scripts/ebay_api_ingest.py`**

```python
"""
eBay Finding API / Browse API で listing を取得。
正式 API 主ルート（OAuth Bearer token 使用）。
"""

def fetch_ebay_listings_by_keywords(keywords: list[str]) -> list[dict]:
    """Finding API: findItemsAdvanced"""

def fetch_ebay_item_details(item_id: str) -> dict:
    """Browse API: getItem"""

def upsert_listing_to_raw(listing: dict) -> str:
    """ebay_listings_raw に upsert。listing_id を返す"""

def save_listing_snapshot(listing_id: str, price: float, bid_count: int,
                          time_left_seconds: int) -> None:
    """ebay_listing_snapshots に time-series 保存"""
```

**`coin_business/scripts/ebay_seed_scanner.py`**

```python
"""
yahoo_coin_seeds を使って eBay を全域スキャン。
seed ごとに検索し ebay_seed_hits を更新。
"""

def scan_ebay_for_seed(seed_id: str) -> dict:
    """
    seed の検索クエリで eBay API 検索。
    ヒットした listing を ebay_seed_hits に登録。
    Returns: {"hits": N}
    """

def run_all_seed_scans() -> dict:
    """全 seed をスキャン。Returns: {"total_seeds": N, "total_hits": N}"""
```

**完了条件**: eBay listing が継続取得できる。raw / snapshot / seed_hit が埋まる。

---

### Phase 6 — 世界オークション event / lot 収集

**担当**: CAP Backend / CAP Automation
**期間**: Day25〜Day30（4-6日）

#### 新テーブル

- `global_auction_events` → `migrations/015_global_auction_tables.sql`
- `global_auction_lots` → 同上

#### 新スクリプト

**`coin_business/scripts/global_auction_sync.py`**

```python
"""
Heritage / Spink / Stack's Bowers / Noble の
auction event 情報を取得・台帳化。
"""

SUPPORTED_HOUSES = ["heritage", "spink", "stacks_bowers", "noble"]

def sync_auction_events(house: str) -> dict:
    """
    対象オークションハウスの upcoming events を取得。
    global_auction_events に upsert。
    Returns: {"upserted": N}
    """
```

**`coin_business/scripts/global_lot_ingest.py`**

```python
"""
global_auction_events の公開 lot を事前収集。
T-minus 運用: T-21/T-7/T-3/T-1 で優先更新。
"""

T_MINUS_DAYS = [21, 7, 3, 1]

def ingest_lots_for_event(event_id: str) -> dict:
    """
    event の公開 lot を取得し global_auction_lots に upsert。
    Returns: {"upserted": N, "new": N}
    """

def refresh_t_minus_lots() -> dict:
    """
    T-21/T-7/T-3/T-1 に該当する event の lot を優先 refresh。
    """
```

**完了条件**: event と lot が台帳化される。T-minus 監視の基礎ができる。

---

### Phase 7 — BOT抽出 + CAP監査の二重チェック

**担当**: CAP Backend
**期間**: Day28〜Day35（5-7日）

#### 新テーブル

- `candidate_match_results` → `migrations/016_match_audit_watch.sql`

#### 新スクリプト

**`coin_business/scripts/match_engine.py`**

```python
"""
1段目: Yahoo!基準と eBay/lot の機械照合。
Level A 候補を仮生成する。
"""

def match_ebay_listing_to_seeds(listing_id: str) -> list[dict]:
    """
    listing に対して seed を照合。
    Level A 条件を判定。
    Returns: list of match result dicts
    """

def match_global_lot_to_seeds(lot_id: str) -> list[dict]:
    """global lot に対して seed 照合"""

def run_pending_matches() -> dict:
    """未照合 listing/lot を全件照合"""
```

**`coin_business/scripts/cap_audit_runner.py`**

```python
"""
2段目: CAP 監査。
match_engine の仮候補を審査し AUDIT_PASS/HOLD/FAIL を判定。
AUDIT_PASS のみ daily_candidates に昇格。
"""

AUDIT_CHECKS = [
    "cert_validity",        # cert 妥当性
    "title_consistency",    # タイトル整合
    "grade_delta",          # グレード差
    "year_delta",           # 年数差
    "profit_condition",     # 利益条件
    "shipping_valid",       # shipping 条件
    "lot_size_single",      # lot size (単品のみ)
    "not_stale",            # stale でない
    "not_sold",             # sold でない
    "not_ended",            # ended でない
]

def audit_candidate_match(match_id: str) -> str:
    """
    照合結果を審査し AUDIT_PASS / AUDIT_HOLD / AUDIT_FAIL を返す。
    candidate_match_results に結果を保存。
    AUDIT_PASS なら daily_candidates に昇格。
    """

def run_pending_audits() -> dict:
    """未審査の match_result を全件審査"""
```

**完了条件**: BOT 抽出単独では候補化されない。AUDIT_PASS のみ候補化される。監査ログが残る。

---

### Phase 8 — pricing / target bid / KEEP監視

**担当**: CAP Backend / CAP UI
**期間**: Day32〜Day40（6-9日）

#### 新テーブル

- `candidate_watchlist` → `migrations/016_match_audit_watch.sql`（Phase 7 と同ファイル）

#### 新スクリプト

**`coin_business/scripts/keep_watch_refresher.py`**

```python
"""
candidate_watchlist に登録された候補を
残時間に応じた頻度で自動 refresh するジョブ。
"""

from scripts.constants import KEEP_WATCH_INTERVALS

def get_candidates_due_for_refresh() -> list[dict]:
    """
    watchlist の候補を残時間に応じた refresh 優先度でソートして返す。
    """

def refresh_watchlist_candidate(watchlist_id: str) -> dict:
    """
    候補の status / price / time_left を更新。
    diff があれば snapshot 保存。
    BID_READY 条件を満たせばフラグを立てる。
    Returns: {"status": "updated"|"unchanged"|"ended"}
    """

def run_due_refreshes() -> dict:
    """期限を過ぎた全 watchlist 候補を refresh"""
```

#### dashboard.py タブ追加 — `render_watchlist_tab()`

```python
def render_watchlist_tab():
    st.header("👁 KEEP監視リスト")

    items = load_watchlist()  # candidate_watchlist を取得

    for item in items:
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.write(f"**{item['title']}**")
                st.write(f"残時間: {item['time_left_display']} | "
                         f"現在値: {item['current_price_display']} | "
                         f"Max Bid: ¥{item.get('max_bid_jpy', 0):,}")
            with col2:
                if item.get('is_bid_ready'):
                    st.success("🟢 BID_READY")
                else:
                    st.info("👁 監視中")
            with col3:
                if st.button("入札キュー", key=f"wl_{item['id']}"):
                    queue_candidate_for_bid(item['candidate_id'])
                    st.rerun()
```

**完了条件**: max bid が入る。KEEP 後 watchlist に入る。状況差分が保存される。

---

### Phase 9 — Slack / Notion / Dashboard 統合

**担当**: CAP Automation / CAP UI
**期間**: Day38〜Day45（5-8日）

#### 新スクリプト

**`coin_business/scripts/slack_notifier.py`**

```python
"""
Slack Incoming Webhooks で定型通知を送るモジュール。
"""

import os, json, requests
from datetime import datetime

SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK_URL")


def send_morning_brief(stats: dict) -> bool:
    """
    朝ブリーフ通知。毎朝8時頃に自動送信。
    stats: {"review_ng": N, "bid_ready": N, "watch": N, "pricing_missing": N}
    """
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
            "text": f"📊 コイン仕入れ朝ブリーフ {datetime.now().strftime('%m/%d')}"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*REVIEW_NG*\n{stats['review_ng']}件"},
            {"type": "mrkdwn", "text": f"*BID_READY*\n{stats['bid_ready']}件"},
            {"type": "mrkdwn", "text": f"*KEEP監視中*\n{stats['watch']}件"},
            {"type": "mrkdwn", "text": f"*pricing未確定*\n{stats['pricing_missing']}件"},
        ]},
    ]
    return _post_blocks(blocks)


def send_level_a_candidate(candidate: dict) -> bool:
    """
    Level A 新規候補通知。
    candidate: daily_candidates の row + audit result
    """
    title = candidate.get('title', '')[:60]
    profit = candidate.get('projected_profit_jpy', 0)
    roi = candidate.get('projected_roi', 0)
    source = candidate.get('source', '')
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🏆 Level A 候補"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{title}*\n"
                    f"予想利益: ¥{profit:,} | ROI: {roi:.1%} | ソース: {source}"}},
    ]
    if candidate.get('source_url'):
        blocks.append({"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "📎 確認"},
             "url": candidate['source_url']}
        ]})
    return _post_blocks(blocks)


def send_keep_price_alert(watchlist_item: dict) -> bool:
    """
    KEEP 候補の価格変化通知。
    """
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "⚠️ KEEP 価格変化"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{watchlist_item['title'][:60]}*\n"
                    f"前回: ¥{watchlist_item.get('prev_price_jpy', 0):,} → "
                    f"現在: ¥{watchlist_item.get('current_price_jpy', 0):,}\n"
                    f"残時間: {watchlist_item.get('time_left_display', '')}"}},
    ]
    return _post_blocks(blocks)


def send_ending_soon_alert(watchlist_item: dict) -> bool:
    """終了間近アラート（1時間以内）"""
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🔔 終了間近"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{watchlist_item['title'][:60]}*\n"
                    f"残り: {watchlist_item.get('time_left_display', '')} | "
                    f"現在値: ¥{watchlist_item.get('current_price_jpy', 0):,}"}},
    ]
    return _post_blocks(blocks)


def send_bid_ready(candidate: dict) -> bool:
    """BID_READY 通知 — Dashboard で確認を促す"""
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🟢 BID_READY"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{candidate.get('title', '')[:60]}*\n"
                    f"Max Bid: ¥{candidate.get('recommended_max_bid_jpy', 0):,} | "
                    f"ROI: {candidate.get('projected_roi', 0):.1%}"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "👉 Dashboard の「KEEP監視」タブで入札キューへ送信してください"}},
    ]
    return _post_blocks(blocks)


def send_global_auction_lot(lot: dict) -> bool:
    """世界オークション注目 lot 通知"""
    blocks = [
        {"type": "header", "text": {"type": "plain_text",
            "text": f"🌏 {lot.get('auction_house', '')} 注目 lot"}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{lot.get('lot_title', '')[:60]}*\n"
                    f"開催: {lot.get('auction_date_str', '')} | "
                    f"推定落札: {lot.get('estimate_display', '')}"}},
    ]
    return _post_blocks(blocks)


def _post_blocks(blocks: list) -> bool:
    if not SLACK_WEBHOOK:
        return False
    try:
        r = requests.post(SLACK_WEBHOOK, json={"blocks": blocks}, timeout=10)
        return r.status_code == 200
    except Exception:
        return False
```

**`coin_business/scripts/notion_sync.py`**

```python
"""
Notion API で台帳・スケジュール・イベント管理を行うモジュール。
読み書きは Notion Databases API 経由。
"""

import os, requests

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_API = "https://api.notion.com/v1"
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}

# DB IDs は env で管理
BID_LEDGER_DB = os.getenv("NOTION_BID_LEDGER_DB_ID")
SCHEDULE_DB   = os.getenv("NOTION_SCHEDULE_DB_ID")
EVENT_DB      = os.getenv("NOTION_EVENT_DB_ID")


def log_bid_to_ledger(candidate: dict, bid_amount_jpy: int) -> str | None:
    """
    入札記録を Notion 台帳 DB に追加。
    Returns: notion page_id or None
    """

def update_bid_result(page_id: str, result: str,
                      final_price_jpy: int | None = None) -> bool:
    """
    入札結果（won/lost）を Notion に更新。
    """

def add_auction_event(event: dict) -> str | None:
    """
    世界オークション event を Notion イベント DB に追加。
    """

def _create_page(db_id: str, properties: dict) -> str | None:
    """Notion DB にページを作成。Returns: page_id"""
    url = f"{NOTION_API}/pages"
    body = {"parent": {"database_id": db_id}, "properties": properties}
    try:
        r = requests.post(url, headers=HEADERS, json=body, timeout=15)
        if r.status_code == 200:
            return r.json().get("id")
    except Exception:
        pass
    return None
```

#### dashboard.py main() — 5タブ構成

```python
def main():
    st.set_page_config(page_title="コイン仕入れ管理", layout="wide")
    st.title("🪙 コイン仕入れダッシュボード")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "📊 候補一覧",
        "🔍 CEO確認",
        "📚 Yahoo!履歴",    # Phase 3 追加
        "👁 KEEP監視",       # Phase 8 追加
        "📋 入札実績",
    ])

    with tab1:
        render_candidate_list_tab()

    with tab2:
        render_ceo_review_tab()

    with tab3:
        render_yahoo_pending_review_tab()  # Phase 3

    with tab4:
        render_watchlist_tab()             # Phase 8

    with tab5:
        render_bid_history_tab()
```

**完了条件**: CEO が Slack + dashboard だけで意思決定できる。Notion に履歴が残る。

---

## Migration ファイル一覧

| ファイル | 内容 |
|---------|------|
| `migrations/012_yahoo_staging.sql` | yahoo_sold_lots_staging, yahoo_sold_lot_reviews |
| `migrations/013_yahoo_seeds.sql` | yahoo_coin_seeds |
| `migrations/014_ebay_listing_tables.sql` | ebay_listings_raw, ebay_listing_snapshots, ebay_seed_hits |
| `migrations/015_global_auction_tables.sql` | global_auction_events, global_auction_lots |
| `migrations/016_match_audit_watch.sql` | candidate_match_results, candidate_watchlist |
| `migrations/017_notifications_negotiate.sql` | negotiate_later, notification_log |

---

## ジョブ実行スケジュール

| ジョブ | スクリプト | 頻度 | Phase |
|--------|----------|------|-------|
| Yahoo!staging 同期 | `yahoo_sold_sync.py` | 毎日 6:00 | 2 |
| 承認済み Yahoo!昇格 | `yahoo_promoter.py` | 毎日 6:30 | 4 |
| seed 生成 | `seed_generator.py` | 毎日 7:00 | 4 |
| eBay seed スキャン | `ebay_seed_scanner.py` | 毎日 8:00 / 14:00 / 20:00 | 5 |
| eBay snapshot 更新 | `ebay_api_ingest.py` | 2時間ごと | 5 |
| 世界オークション event 同期 | `global_auction_sync.py` | 毎日 7:30 | 6 |
| 世界オークション lot 収集 | `global_lot_ingest.py` | 毎日 8:30 / T-minus 優先 | 6 |
| マッチング | `match_engine.py` | 毎日 9:00 | 7 |
| CAP 監査 | `cap_audit_runner.py` | 毎日 9:30 | 7 |
| KEEP watchlist refresh | `keep_watch_refresher.py` | 10分ごと | 8 |
| nightly ops | `nightly_ops.py` | 毎日 3:00 | 既存 |
| 朝ブリーフ Slack | `slack_notifier.py` | 毎日 8:00 | 9 |

---

## フェーズ別 完了条件まとめ

| Phase | 完了条件 |
|-------|---------|
| 1 | 仕様書・定数定義が更新されている |
| 2 | Yahoo!新規履歴が staging に蓄積。本DB汚れなし |
| 3 | CEO が staging をレビューできる。決定が保存される |
| 4 | 未承認履歴は seed に混ざらない |
| 5 | eBay listing が継続取得できる。raw/snapshot/seed_hit が埋まる |
| 6 | event と lot が台帳化される |
| 7 | BOT 抽出単独では候補化されない。AUDIT_PASS のみ候補化 |
| 8 | max bid が入る。KEEP 後 watchlist に入る |
| 9 | CEO が Slack + dashboard だけで意思決定できる |
| 10 | Mode 1→2→3 移行設計が完成している |

---

## env 追加項目

```env
# coin_business/.env に追加
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/XXX/YYY/ZZZ
NOTION_TOKEN=secret_XXXXXXX
NOTION_BID_LEDGER_DB_ID=XXXXXXXX
NOTION_SCHEDULE_DB_ID=XXXXXXXX
NOTION_EVENT_DB_ID=XXXXXXXX
```

---

## CAP最終指示文（引継ぎ用）

> 現在の既存基盤（dashboard、review queue、pricing、nightly ops、bid queue）は維持しつつ、次の主戦略へ移行してください。母集団は Yahoo!落札履歴のみです。Yahoo!履歴は当面は `yahoo_sold_lots_staging` に自動取得し、CEO 承認済みのものだけを `yahoo_sold_lots` に昇格させてください。seed 生成は承認済み Yahoo!履歴のみを使ってください。eBay API 承認は取得済みなので、eBay listing 取得は正式 API を主ルートにしてください。世界オークション lot も事前に収集してください。候補化は Level A のみで、A は cert 完全一致だけでなく、Yahoo 基準より高グレードで利益条件を満たす案件、および前後5年以内で利益条件を満たす年号差案件も含みます。BOT 抽出結果は必ず CAP 監査を通し、AUDIT_PASS だけを `daily_candidates` に昇格させてください。CEO が KEEP した候補は watchlist に登録し、通常3時間ごと、24時間以内は1時間ごと、6時間以内は30分ごと、1時間以内は10分ごとに自動監視してください。通知は Slack、履歴台帳は Notion、最終判断 UI は dashboard に集約してください。BitNow は原則除外ですが、将来交渉用に `NEGOTIATE_LATER` 箱へ保存可能にしてください。最終目標は、CEO が母集団確認や候補精査から離れ、入札・買付だけに集中できる全自動運用です。ただし、その全自動化は必ず BOT 抽出 + CAP 監査の二重チェック前提で実装してください。
