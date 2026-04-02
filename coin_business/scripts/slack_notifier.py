"""
coin_business/scripts/slack_notifier.py
=========================================
Slack 通知の統合モジュール。Phase 1〜9 の全通知種別を扱う。

送信可能な通知種別 (NotificationType):
  morning_brief    : 朝ブリーフ (KPI サマリー)
  level_a_new      : Level A 新規候補
  keep_price_alert : KEEP 候補の価格変化
  ending_soon      : 終了間近 (1時間以内)
  bid_ready        : 入札実行可能状態

重複送信防止:
  notification_log を参照し、同じ通知が within_hours 以内に
  送信済みであればスキップする。

設計:
  - Slack Bot Token (SLACK_BOT_TOKEN) を使用
  - ブロックキット形式で送信
  - 全送信結果を notification_log に記録
  - dry_run=True 時は実際の送信をせずメッセージ内容を返す

CLI:
  python slack_notifier.py morning-brief
  python slack_notifier.py morning-brief --dry-run
  python slack_notifier.py level-a-new   --dry-run
  python slack_notifier.py ending-soon   --dry-run
  python slack_notifier.py bid-ready     --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from constants import AuditStatus, NotificationType, Table, WatchStatus
from db.notification_repo import (
    log_notification,
    record_morning_brief_run,
    was_recently_notified,
)
from scripts.supabase_client import get_client

logger = logging.getLogger(__name__)

# ================================================================
# 環境変数 / Slack 設定
# ================================================================

_ENV_FILE = Path(__file__).parent.parent / ".env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL   = os.environ.get("SLACK_CHANNEL", "C0ALSAPMYHY")  # #ceo-room
SLACK_API_POST  = "https://slack.com/api/chat.postMessage"


# ================================================================
# KPI 取得 (morning brief 用)
# ================================================================

def fetch_kpi(client) -> dict:
    """
    morning brief 用 KPI を取得する。

    Returns dict with keys:
      yahoo_pending_count, audit_pass_count, keep_count,
      bid_ready_count, total_candidates
    """
    kpi: dict = {
        "yahoo_pending_count": 0,
        "audit_pass_count":    0,
        "keep_count":          0,
        "bid_ready_count":     0,
        "total_candidates":    0,
    }
    try:
        # Yahoo staging pending
        r = (
            client.table(Table.YAHOO_SOLD_LOTS_STAGING)
            .select("id", count="exact")
            .eq("status", "PENDING_CEO")
            .execute()
        )
        kpi["yahoo_pending_count"] = r.count or 0
    except Exception as exc:
        logger.warning("KPI yahoo_pending: %s", exc)

    try:
        # AUDIT_PASS 候補数
        r = (
            client.table(Table.DAILY_CANDIDATES)
            .select("id", count="exact")
            .eq("audit_status", AuditStatus.AUDIT_PASS)
            .execute()
        )
        kpi["audit_pass_count"] = r.count or 0
    except Exception as exc:
        logger.warning("KPI audit_pass: %s", exc)

    try:
        # KEEP 件数 (watchlist ACTIVE)
        r = (
            client.table(Table.CANDIDATE_WATCHLIST)
            .select("id", count="exact")
            .in_("status", list(WatchStatus.ACTIVE))
            .execute()
        )
        kpi["keep_count"] = r.count or 0
    except Exception as exc:
        logger.warning("KPI keep_count: %s", exc)

    try:
        # BID_READY 件数
        r = (
            client.table(Table.CANDIDATE_WATCHLIST)
            .select("id", count="exact")
            .eq("status", WatchStatus.BID_READY)
            .execute()
        )
        kpi["bid_ready_count"] = r.count or 0
    except Exception as exc:
        logger.warning("KPI bid_ready: %s", exc)

    try:
        r = (
            client.table(Table.DAILY_CANDIDATES)
            .select("id", count="exact")
            .execute()
        )
        kpi["total_candidates"] = r.count or 0
    except Exception as exc:
        logger.warning("KPI total_candidates: %s", exc)

    return kpi


# ================================================================
# Block Kit ビルダー
# ================================================================

def _build_morning_brief_blocks(kpi: dict, run_date: str) -> list[dict]:
    bid_text = (
        f"🔥 *BID_READY: {kpi['bid_ready_count']}件* — 入札実行可能！"
        if kpi["bid_ready_count"] > 0
        else f"BID_READY: {kpi['bid_ready_count']}件"
    )
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"☀️ 朝ブリーフ {run_date}"},
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Yahoo Pending*\n{kpi['yahoo_pending_count']}件",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*AUDIT_PASS 候補*\n{kpi['audit_pass_count']}件",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*KEEP 監視中*\n{kpi['keep_count']}件",
                },
                {
                    "type": "mrkdwn",
                    "text": bid_text,
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"_SolarWorks AI · coin_business · {run_date}_",
                }
            ],
        },
    ]


def _build_level_a_blocks(candidate: dict) -> list[dict]:
    cid    = candidate.get("id", "")[:8]
    title  = candidate.get("title") or candidate.get("lot_title") or "不明"
    target = candidate.get("target_max_bid_jpy")
    score  = candidate.get("comparison_quality_score")
    target_str = f"¥{target:,}" if target else "未計算"
    score_str  = f"{score:.2f}" if score is not None else "-"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔔 Level A 新規候補"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{title}*\n"
                    f"target_max_bid: {target_str} | 品質スコア: {score_str}\n"
                    f"`id: {cid}...`"
                ),
            },
        },
    ]


def _build_keep_price_alert_blocks(item: dict) -> list[dict]:
    wid    = item.get("id", "")[:8]
    status = item.get("status", "")
    price  = item.get("current_price_jpy")
    max_b  = item.get("max_bid_jpy")
    price_str = f"¥{price:,}" if price else "-"
    max_str   = f"¥{max_b:,}" if max_b else "-"
    emoji     = "✅" if status == WatchStatus.PRICE_OK else "⚠️"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} KEEP 価格変化"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*ステータス*\n{status}"},
                {"type": "mrkdwn", "text": f"*現在価格*\n{price_str}"},
                {"type": "mrkdwn", "text": f"*入札上限*\n{max_str}"},
                {"type": "mrkdwn", "text": f"*watchlist ID*\n`{wid}...`"},
            ],
        },
    ]


def _build_ending_soon_blocks(item: dict) -> list[dict]:
    wid       = item.get("id", "")[:8]
    time_left = item.get("time_left_seconds")
    price     = item.get("current_price_jpy")
    end_at    = item.get("auction_end_at", "")[:16]
    time_str  = (
        f"{time_left // 60}分" if time_left else "不明"
    )
    price_str = f"¥{price:,}" if price else "-"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "⏰ 終了間近 (1時間以内)"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*残り時間*\n{time_str}"},
                {"type": "mrkdwn", "text": f"*現在価格*\n{price_str}"},
                {"type": "mrkdwn", "text": f"*終了時刻*\n{end_at}"},
                {"type": "mrkdwn", "text": f"*watchlist ID*\n`{wid}...`"},
            ],
        },
    ]


def _build_bid_ready_blocks(item: dict) -> list[dict]:
    wid     = item.get("id", "")[:8]
    price   = item.get("current_price_jpy")
    max_b   = item.get("max_bid_jpy")
    reason  = item.get("bid_ready_reason", "price_ok_within_1h")
    p_str   = f"¥{price:,}" if price else "-"
    m_str   = f"¥{max_b:,}" if max_b else "-"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🟢 BID_READY — 入札実行可能！"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*現在価格*\n{p_str}"},
                {"type": "mrkdwn", "text": f"*入札上限*\n{m_str}"},
                {"type": "mrkdwn", "text": f"*判定理由*\n{reason}"},
                {"type": "mrkdwn", "text": f"*watchlist ID*\n`{wid}...`"},
            ],
        },
    ]


# ================================================================
# Slack HTTP 送信
# ================================================================

@dataclass
class SendResult:
    ok:        bool
    message_ts: Optional[str] = None
    error:     Optional[str]  = None


def _post_to_slack(blocks: list[dict], text: str) -> SendResult:
    """
    Slack Bot Token を使い chat.postMessage を呼び出す。
    """
    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN が設定されていません — 送信スキップ")
        return SendResult(ok=False, error="no_token")

    payload = json.dumps({
        "channel": SLACK_CHANNEL,
        "blocks":  blocks,
        "text":    text,          # fallback text (通知バナー用)
    }).encode("utf-8")

    req = urllib.request.Request(
        SLACK_API_POST,
        data    = payload,
        headers = {
            "Content-Type":  "application/json; charset=utf-8",
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
        },
        method = "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if body.get("ok"):
            return SendResult(ok=True, message_ts=body.get("ts"))
        return SendResult(ok=False, error=body.get("error", "unknown"))
    except urllib.error.URLError as exc:
        return SendResult(ok=False, error=str(exc))


# ================================================================
# 通知関数
# ================================================================

def notify_morning_brief(
    client,
    *,
    dry_run: bool = False,
) -> dict:
    """
    朝ブリーフを Slack に送信する。
    重複防止: 本日分が既に送信済みなら skip。

    Returns: {"status": "sent"|"skipped"|"failed", "kpi": {...}}
    """
    run_date = date.today().isoformat()

    # dedup: 本日の morning_brief が既に sent か
    already = was_recently_notified(
        client,
        NotificationType.MORNING_BRIEF,
        within_hours=20.0,  # 20h → 実質1日1回
    )
    if already and not dry_run:
        logger.info("morning_brief already sent today — skip")
        return {"status": "skipped", "kpi": {}}

    kpi    = fetch_kpi(client)
    blocks = _build_morning_brief_blocks(kpi, run_date)
    text   = f"☀️ 朝ブリーフ {run_date} — Yahoo pending:{kpi['yahoo_pending_count']} PASS:{kpi['audit_pass_count']} KEEP:{kpi['keep_count']} BID_READY:{kpi['bid_ready_count']}"

    if dry_run:
        logger.info("[DRY-RUN] morning_brief: %s", text)
        return {"status": "dry_run", "kpi": kpi, "blocks": blocks}

    result = _post_to_slack(blocks, text)
    status = "sent" if result.ok else "failed"

    log_notification(
        client,
        notification_type = NotificationType.MORNING_BRIEF,
        message_summary   = text,
        payload           = {"blocks": blocks, "kpi": kpi},
        status            = status,
        error_message     = result.error,
    )
    record_morning_brief_run(
        client,
        run_date           = run_date,
        status             = status,
        yahoo_pending_count= kpi["yahoo_pending_count"],
        audit_pass_count   = kpi["audit_pass_count"],
        keep_count         = kpi["keep_count"],
        bid_ready_count    = kpi["bid_ready_count"],
        slack_message_ts   = result.message_ts,
        error_message      = result.error,
    )
    return {"status": status, "kpi": kpi}


def notify_level_a_new(
    client,
    candidate: dict,
    *,
    dry_run: bool = False,
) -> str:
    """
    Level A 新規候補を通知する。
    重複防止: 同 candidate_id が 24h 以内に送信済みなら skip。

    Returns: "sent" | "skipped" | "failed" | "dry_run"
    """
    cid = candidate.get("id")
    if was_recently_notified(
        client, NotificationType.LEVEL_A_NEW,
        candidate_id=cid, within_hours=24.0,
    ) and not dry_run:
        return "skipped"

    blocks = _build_level_a_blocks(candidate)
    title  = candidate.get("title") or candidate.get("lot_title") or "不明"
    text   = f"🔔 Level A 新規候補: {title}"

    if dry_run:
        logger.info("[DRY-RUN] level_a_new: %s", text)
        return "dry_run"

    result = _post_to_slack(blocks, text)
    status = "sent" if result.ok else "failed"
    log_notification(
        client,
        notification_type = NotificationType.LEVEL_A_NEW,
        candidate_id      = cid,
        message_summary   = text,
        payload           = {"blocks": blocks},
        status            = status,
        error_message     = result.error,
    )
    return status


def notify_keep_price_alert(
    client,
    item: dict,
    *,
    dry_run: bool = False,
) -> str:
    """
    KEEP 監視中アイテムの価格変化を通知する。
    重複防止: 同 watchlist_id が 2h 以内に送信済みなら skip。

    Returns: "sent" | "skipped" | "failed" | "dry_run"
    """
    wid = item.get("id")
    if was_recently_notified(
        client, NotificationType.KEEP_PRICE_ALERT,
        watchlist_id=wid, within_hours=2.0,
    ) and not dry_run:
        return "skipped"

    blocks = _build_keep_price_alert_blocks(item)
    text   = f"⚠️ KEEP 価格変化: {item.get('status')} wid={str(wid or '')[:8]}"

    if dry_run:
        logger.info("[DRY-RUN] keep_price_alert: %s", text)
        return "dry_run"

    result = _post_to_slack(blocks, text)
    status = "sent" if result.ok else "failed"
    log_notification(
        client,
        notification_type = NotificationType.KEEP_PRICE_ALERT,
        watchlist_id      = wid,
        message_summary   = text,
        payload           = {"blocks": blocks},
        status            = status,
        error_message     = result.error,
    )
    return status


def notify_ending_soon(
    client,
    item: dict,
    *,
    dry_run: bool = False,
) -> str:
    """
    終了間近 (1時間以内) の watchlist アイテムを通知する。
    重複防止: 同 watchlist_id が 1h 以内に送信済みなら skip。

    Returns: "sent" | "skipped" | "failed" | "dry_run"
    """
    wid = item.get("id")
    if was_recently_notified(
        client, NotificationType.ENDING_SOON,
        watchlist_id=wid, within_hours=1.0,
    ) and not dry_run:
        return "skipped"

    blocks = _build_ending_soon_blocks(item)
    time_left = item.get("time_left_seconds")
    time_str  = f"{time_left // 60}分" if time_left else "不明"
    text      = f"⏰ 終了間近 残り{time_str} wid={str(wid or '')[:8]}"

    if dry_run:
        logger.info("[DRY-RUN] ending_soon: %s", text)
        return "dry_run"

    result = _post_to_slack(blocks, text)
    status = "sent" if result.ok else "failed"
    log_notification(
        client,
        notification_type = NotificationType.ENDING_SOON,
        watchlist_id      = wid,
        message_summary   = text,
        payload           = {"blocks": blocks},
        status            = status,
        error_message     = result.error,
    )
    return status


def notify_bid_ready(
    client,
    item: dict,
    *,
    dry_run: bool = False,
) -> str:
    """
    BID_READY 状態の watchlist アイテムを通知する。
    重複防止: 同 watchlist_id が 30min 以内に送信済みなら skip。

    Returns: "sent" | "skipped" | "failed" | "dry_run"
    """
    wid = item.get("id")
    if was_recently_notified(
        client, NotificationType.BID_READY,
        watchlist_id=wid, within_hours=0.5,
    ) and not dry_run:
        return "skipped"

    blocks = _build_bid_ready_blocks(item)
    price  = item.get("current_price_jpy")
    text   = (
        f"🟢 BID_READY ¥{price:,} wid={str(wid or '')[:8]}"
        if price else
        f"🟢 BID_READY wid={str(wid or '')[:8]}"
    )

    if dry_run:
        logger.info("[DRY-RUN] bid_ready: %s", text)
        return "dry_run"

    result = _post_to_slack(blocks, text)
    status = "sent" if result.ok else "failed"
    log_notification(
        client,
        notification_type = NotificationType.BID_READY,
        watchlist_id      = wid,
        message_summary   = text,
        payload           = {"blocks": blocks},
        status            = status,
        error_message     = result.error,
    )
    return status


# ================================================================
# CLI
# ================================================================

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Slack 通知を送信する"
    )
    parser.add_argument(
        "type",
        choices=["morning-brief", "level-a-new", "keep-price", "ending-soon", "bid-ready"],
        help="通知種別",
    )
    parser.add_argument("--dry-run", action="store_true", help="実際には送信しない")
    args = parser.parse_args()

    client = get_client()

    if args.type == "morning-brief":
        r = notify_morning_brief(client, dry_run=args.dry_run)
        print(f"morning_brief: {r['status']} kpi={r.get('kpi', {})}")

    elif args.type == "ending-soon":
        # watchlist から ending_soon アイテムを取得して送信
        try:
            res = (
                client.table(Table.CANDIDATE_WATCHLIST)
                .select("*")
                .eq("status", WatchStatus.ENDING_SOON)
                .limit(10)
                .execute()
            )
            items = res.data or []
        except Exception:
            items = []
        for item in items:
            s = notify_ending_soon(client, item, dry_run=args.dry_run)
            print(f"  ending_soon wid={str(item.get('id',''))[:8]}: {s}")
        if not items:
            print("ending_soon: 対象なし")

    elif args.type == "bid-ready":
        try:
            res = (
                client.table(Table.CANDIDATE_WATCHLIST)
                .select("*")
                .eq("status", WatchStatus.BID_READY)
                .limit(10)
                .execute()
            )
            items = res.data or []
        except Exception:
            items = []
        for item in items:
            s = notify_bid_ready(client, item, dry_run=args.dry_run)
            print(f"  bid_ready wid={str(item.get('id',''))[:8]}: {s}")
        if not items:
            print("bid_ready: 対象なし")

    else:
        print(f"{args.type}: このコマンドは run.py から呼び出してください")


if __name__ == "__main__":
    main()
