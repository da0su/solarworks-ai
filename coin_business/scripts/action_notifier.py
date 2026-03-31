"""
action_notifier.py  ─  Layer 4: アクションレイヤー

daily_candidates に候補が入った後の「意思決定加速」ロジック。

判定ルーティング:
  OK     → 優先フラグ付与 + Slack 強通知 (#ceo-room, <!channel>)
  REVIEW → Slack 通常通知 (CAPレビュー待ち)
  NG     → DB保存のみ（無音）
  CEO判断 → CEO専用リスト分離 + Slack 強通知

CEOに上げる条件 (いずれか1つでも該当):
  ① estimated_cost_jpy > 500,000          高額仕入れ
  ② match_score < 0.75                    コイン照合が不確実
  ③ yahoo_3m_count < 5                    直近3か月市場データ不足
  ④ estimated_margin_pct > 2.0            余裕>200%（データ誤り疑い）
  ⑤ priority == 3 かつ estimated_cost_jpy > 300,000  最重要オークション×中高額

使い方:
  from scripts.action_notifier import route_and_notify, JudgmentResult

  result = route_and_notify(candidate_record, dry_run=False)
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── .env 読み込み (ルート + coin_business 両方) ───────────────────
_COIN_ENV  = Path(__file__).parent.parent / ".env"
_ROOT_ENV  = Path(__file__).parent.parent.parent / ".env"

for _env_path in [_ROOT_ENV, _COIN_ENV]:
    if _env_path.exists():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ── Slack 設定 ────────────────────────────────────────────────────
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CEO_ROOM_CHANNEL = "C0ALSAPMYHY"   # #ceo-room
SLACK_API_POST   = "https://slack.com/api/chat.postMessage"

# ── 判定閾値 ──────────────────────────────────────────────────────
CEO_THRESHOLD_COST_JPY    = 500_000   # ① 高額閾値
CEO_THRESHOLD_MATCH_SCORE = 0.75      # ② 照合スコア下限
CEO_THRESHOLD_3M_COUNT    = 5         # ③ 直近3か月データ最低件数
CEO_THRESHOLD_MARGIN      = 2.0       # ④ 余裕率上限 (200%)
CEO_THRESHOLD_P3_COST_JPY = 300_000   # ⑤ P3オークション高額閾値


# ── 判定ロジック ──────────────────────────────────────────────────

def decide_judgment(candidate: dict) -> tuple[str, str]:
    """
    overseasロット候補レコードを受け取り、judgment と judgment_reason を返す。

    【判定ロジック v2 (2026-04-01改訂)】
    ref1/ref2がある場合: current_price_jpy を基準値と直接比較
      - current_price_jpy > max(ref1,ref2) → NG
      - current_price_jpy > min(ref1,ref2) → WARNING / CEO判断
      - current_price_jpy ≤ min(ref1,ref2) → OK / CEO判断
    NGC補正: NGC鑑定品はヤフオク売値×0.92 → ref1/ref2も×0.92で実効計算
    ref1/ref2なし: 従来の buy_limit_jpy / estimated_cost_jpy ロジックにフォールバック

    Returns:
        (judgment, reason)
        judgment: "OK" | "WARNING" | "NG" | "CEO判断"
    """
    import re as _re

    cur_price_jpy = float(candidate.get("current_price") or 0)   # eBay現在入札価格（円換算）
    cost_jpy      = float(candidate.get("estimated_cost_jpy") or 0)
    buy_limit     = float(candidate.get("buy_limit_jpy") or 0)
    ref1_raw      = candidate.get("ref1_buy_limit_20k_jpy")
    ref2_raw      = candidate.get("ref2_buy_limit_20k_jpy")
    match_score   = float(candidate.get("match_score") or 0)
    margin_pct    = float(candidate.get("estimated_margin_pct") or 0)
    yahoo_3m      = int(candidate.get("yahoo_3m_count") or 0)
    priority      = int(candidate.get("priority") or 1)
    lot_title     = candidate.get("lot_title") or ""

    # NGC補正: NGC鑑定品はヤフオク売値×0.92 → ref1/ref2も×0.92で実効上限を算出
    is_ngc     = bool(_re.search(r'\bNGC\b', lot_title, _re.IGNORECASE))
    ngc_factor = 0.92 if is_ngc else 1.0
    ngc_tag    = " [NGC×0.92補正]" if is_ngc else ""

    # ── v2 判定: ref1/ref2 が揃っている場合 ───────────────────────
    if ref1_raw is not None and ref2_raw is not None:
        eff_ref1    = float(ref1_raw) * ngc_factor
        eff_ref2    = float(ref2_raw) * ngc_factor
        loose_bound = max(eff_ref1, eff_ref2)   # 超えたらNG
        tight_bound = min(eff_ref1, eff_ref2)   # 超えたらWARNING

        # NG: 現在価格が最大基準を超過
        if cur_price_jpy > loose_bound:
            return "NG", (
                f"現在価格超過NG (¥{cur_price_jpy:,.0f} > "
                f"上限¥{loose_bound:,.0f}{ngc_tag})"
            )

        # WARNING域: tight〜looseの間
        if cur_price_jpy > tight_bound:
            ceo_flags = []
            if cost_jpy > CEO_THRESHOLD_COST_JPY:
                ceo_flags.append(f"高額仕入れ (¥{cost_jpy:,.0f})")
            if 0 < match_score < CEO_THRESHOLD_MATCH_SCORE:
                ceo_flags.append(f"照合スコア不確実 ({match_score:.2f})")
            if 0 < yahoo_3m < CEO_THRESHOLD_3M_COUNT:
                ceo_flags.append(f"直近3か月データ不足 ({yahoo_3m}件)")
            if priority == 3 and cost_jpy > CEO_THRESHOLD_P3_COST_JPY:
                ceo_flags.append("最重要P3×中高額")
            if ceo_flags:
                return "CEO判断", (
                    f"WARNING域+要確認 (¥{cur_price_jpy:,.0f} > 基準¥{tight_bound:,.0f}{ngc_tag})"
                    f" / " + " / ".join(ceo_flags)
                )
            return "WARNING", (
                f"要注意: 現在価格¥{cur_price_jpy:,.0f} が基準¥{tight_bound:,.0f}{ngc_tag}"
                f" 超過 (上限¥{loose_bound:,.0f}以内)"
            )

        # OK域: tight以下 → CEO判断条件チェック
        ceo_flags = []
        if cost_jpy > CEO_THRESHOLD_COST_JPY:
            ceo_flags.append(f"高額仕入れ (¥{cost_jpy:,.0f} > ¥{CEO_THRESHOLD_COST_JPY:,.0f})")
        if 0 < match_score < CEO_THRESHOLD_MATCH_SCORE:
            ceo_flags.append(f"照合スコア不確実 ({match_score:.2f} < {CEO_THRESHOLD_MATCH_SCORE})")
        if 0 < yahoo_3m < CEO_THRESHOLD_3M_COUNT:
            ceo_flags.append(f"直近3か月データ不足 ({yahoo_3m}件 < {CEO_THRESHOLD_3M_COUNT}件)")
        if margin_pct > CEO_THRESHOLD_MARGIN:
            ceo_flags.append(f"余裕率異常 ({margin_pct:.1%} > {CEO_THRESHOLD_MARGIN:.0%})")
        if priority == 3 and cost_jpy > CEO_THRESHOLD_P3_COST_JPY:
            ceo_flags.append(f"最重要P3オークション×中高額 (¥{cost_jpy:,.0f})")
        if ceo_flags:
            return "CEO判断", " / ".join(ceo_flags)

        return "OK", (
            f"仕入条件充足: ¥{cur_price_jpy:,.0f} ≤ 基準¥{tight_bound:,.0f}{ngc_tag}"
            f" (上限¥{loose_bound:,.0f})"
        )

    # ── フォールバック: ref1/ref2なし → 従来の buy_limit ロジック ──
    if match_score > 0 and match_score < 0.50:
        return "NG", f"コイン照合スコア低すぎ ({match_score:.2f})"

    if buy_limit > 0 and cost_jpy > buy_limit:
        return "NG", f"採算割れ (推定仕入: ¥{cost_jpy:,.0f} > 上限: ¥{buy_limit:,.0f})"

    ceo_flags = []
    if cost_jpy > CEO_THRESHOLD_COST_JPY:
        ceo_flags.append(f"高額仕入れ (¥{cost_jpy:,.0f} > ¥{CEO_THRESHOLD_COST_JPY:,.0f})")
    if 0 < match_score < CEO_THRESHOLD_MATCH_SCORE:
        ceo_flags.append(f"照合スコア不確実 ({match_score:.2f} < {CEO_THRESHOLD_MATCH_SCORE})")
    if 0 < yahoo_3m < CEO_THRESHOLD_3M_COUNT:
        ceo_flags.append(f"直近3か月データ不足 ({yahoo_3m}件 < {CEO_THRESHOLD_3M_COUNT}件)")
    if margin_pct > CEO_THRESHOLD_MARGIN:
        ceo_flags.append(f"余裕率異常 ({margin_pct:.1%} > {CEO_THRESHOLD_MARGIN:.0%} = データ誤り疑い)")
    if priority == 3 and cost_jpy > CEO_THRESHOLD_P3_COST_JPY:
        ceo_flags.append(f"最重要P3オークション×中高額 (¥{cost_jpy:,.0f})")
    if ceo_flags:
        return "CEO判断", " / ".join(ceo_flags)

    ok_conditions = [
        match_score >= CEO_THRESHOLD_MATCH_SCORE or match_score == 0,
        yahoo_3m >= CEO_THRESHOLD_3M_COUNT or yahoo_3m == 0,
        0 < margin_pct <= CEO_THRESHOLD_MARGIN,
        cost_jpy > 0,
        buy_limit > 0,
    ]
    if all(ok_conditions):
        margin_str = f"利益率 {margin_pct:.1%}" if margin_pct > 0 else ""
        return "OK", f"仕入条件充足 ({margin_str} / 上限まで ¥{buy_limit - cost_jpy:,.0f} 余裕)"

    review_reasons = []
    if 0 < match_score < CEO_THRESHOLD_MATCH_SCORE:
        review_reasons.append(f"照合スコア要確認 ({match_score:.2f})")
    if 0 < yahoo_3m < CEO_THRESHOLD_3M_COUNT:
        review_reasons.append(f"相場データ少ない ({yahoo_3m}件)")
    if margin_pct <= 0:
        review_reasons.append("利益率未計算")
    if not review_reasons:
        review_reasons.append("目視確認推奨")

    return "REVIEW", " / ".join(review_reasons)


# ── Slack 通知 ────────────────────────────────────────────────────

def _slack_post(text: str, channel: str = CEO_ROOM_CHANNEL) -> bool:
    """Slack Bot Token でメッセージ送信。失敗しても例外を投げない。"""
    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN 未設定 — Slack通知スキップ")
        return False
    try:
        payload = json.dumps({"channel": channel, "text": text}).encode("utf-8")
        req = urllib.request.Request(
            SLACK_API_POST,
            data=payload,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if not result.get("ok"):
                logger.warning(f"Slack API error: {result.get('error')}")
                return False
            return True
    except Exception as e:
        logger.warning(f"Slack post error: {e}")
        return False


def _format_ok_message(c: dict) -> str:
    """OK案件の強通知メッセージ。"""
    lot_title  = c.get("lot_title") or c.get("lot_number") or "不明"
    auction    = c.get("auction_house") or c.get("auction_id") or ""
    lot_url    = c.get("lot_url") or ""
    mgmt_no    = c.get("management_no") or "未照合"
    cost       = float(c.get("estimated_cost_jpy") or 0)
    buy_limit  = float(c.get("buy_limit_jpy") or 0)
    margin_pct = float(c.get("estimated_margin_pct") or 0)
    currency   = c.get("currency") or "USD"
    cur_price  = float(c.get("current_price") or 0)
    reason     = c.get("judgment_reason") or ""
    end_time   = c.get("lot_end_time") or c.get("end_date") or ""
    priority   = c.get("priority") or 1

    prio_label = {1: "", 2: " [重要]", 3: " [最重要P3]"}.get(priority, "")

    lines = [
        f"<!channel> 🟢 *仕入れ候補 [OK]{prio_label}* — 即アクション推奨",
        "",
        f"🪙  {lot_title[:60]}",
        f"💰  現在価格: {currency} {cur_price:,.0f}  (推定仕入: ¥{cost:,.0f})",
        f"📊  仕入上限: ¥{buy_limit:,.0f}  |  余裕: +¥{buy_limit - cost:,.0f} ({margin_pct:.1%})",
    ]
    if end_time:
        lines.append(f"⏰  終了: {end_time}")
    lines.append(f"🏛  {auction}")
    if lot_url:
        lines.append(f"🔗  {lot_url}")
    lines.append(f"📌  管理番号: {mgmt_no}  |  {reason}")
    lines.append("")
    lines.append(f"→ 確認: `python run.py overseas-watch --detail {mgmt_no}`")
    return "\n".join(lines)


def _format_review_message(c: dict) -> str:
    """REVIEW案件の通常通知メッセージ。"""
    lot_title  = c.get("lot_title") or c.get("lot_number") or "不明"
    auction    = c.get("auction_house") or c.get("auction_id") or ""
    lot_url    = c.get("lot_url") or ""
    mgmt_no    = c.get("management_no") or "未照合"
    cost       = float(c.get("estimated_cost_jpy") or 0)
    buy_limit  = float(c.get("buy_limit_jpy") or 0)
    reason     = c.get("judgment_reason") or ""
    end_time   = c.get("lot_end_time") or c.get("end_date") or ""

    lines = [
        "🟡 *仕入れ候補 [要確認]* — CAPレビュー待ち",
        "",
        f"🪙  {lot_title[:60]}",
        f"💰  推定仕入: ¥{cost:,.0f}  |  上限: ¥{buy_limit:,.0f}",
        f"⚠️  {reason}",
    ]
    if end_time:
        lines.append(f"⏰  終了: {end_time}")
    lines.append(f"🏛  {auction}")
    if lot_url:
        lines.append(f"🔗  {lot_url}")
    lines.append(f"📌  管理番号: {mgmt_no}")
    return "\n".join(lines)


def _format_ceo_message(c: dict) -> str:
    """CEO判断案件の通知メッセージ。"""
    lot_title  = c.get("lot_title") or c.get("lot_number") or "不明"
    auction    = c.get("auction_house") or c.get("auction_id") or ""
    lot_url    = c.get("lot_url") or ""
    mgmt_no    = c.get("management_no") or "未照合"
    cost       = float(c.get("estimated_cost_jpy") or 0)
    buy_limit  = float(c.get("buy_limit_jpy") or 0)
    reason     = c.get("judgment_reason") or ""
    end_time   = c.get("lot_end_time") or c.get("end_date") or ""

    lines = [
        "<!channel> 📋 *CEO判断リスト — 新規追加*",
        "",
        f"🪙  {lot_title[:60]}",
        f"💰  推定仕入: ¥{cost:,.0f}  |  上限: ¥{buy_limit:,.0f}",
        f"📌  判断理由: {reason}",
    ]
    if end_time:
        lines.append(f"⏰  終了: {end_time}")
    lines.append(f"🏛  {auction}")
    if lot_url:
        lines.append(f"🔗  {lot_url}")
    lines.append(f"管理番号: {mgmt_no}")
    lines.append("")
    lines.append("→ `python run.py overseas-watch --ceo-list` で一覧確認")
    return "\n".join(lines)


# ── バッチ通知（複数候補をまとめて通知） ────────────────────────

def notify_batch(
    ok_list: list[dict],
    review_list: list[dict],
    ceo_list: list[dict],
    dry_run: bool = False,
) -> dict:
    """
    セッション内の全候補をまとめてSlack通知。
    OK/REVIEW/CEO判断それぞれのリストを受け取り通知する。

    Returns: {"ok_sent": int, "review_sent": int, "ceo_sent": int}
    """
    sent = {"ok_sent": 0, "review_sent": 0, "ceo_sent": 0}

    # ── OK: 1件ずつ個別通知（即アクション用）
    for c in ok_list:
        msg = _format_ok_message(c)
        if dry_run:
            logger.info(f"[DRY-RUN] OK通知:\n{msg}")
            sent["ok_sent"] += 1
        elif _slack_post(msg):
            sent["ok_sent"] += 1
            logger.info(f"  [OK通知] {c.get('lot_title', '')[:40]}")

    # ── REVIEW: まとめて1メッセージ（3件以上の場合）
    if review_list:
        if len(review_list) <= 2:
            for c in review_list:
                msg = _format_review_message(c)
                if dry_run:
                    logger.info(f"[DRY-RUN] REVIEW通知:\n{msg}")
                    sent["review_sent"] += 1
                elif _slack_post(msg):
                    sent["review_sent"] += 1
        else:
            # 3件以上はサマリー通知
            lines = [f"🟡 *仕入れ候補 [要確認] {len(review_list)}件* — CAPレビュー待ち", ""]
            for i, c in enumerate(review_list[:10], 1):
                lot = c.get("lot_title") or c.get("lot_number") or "?"
                cost = float(c.get("estimated_cost_jpy") or 0)
                reason = c.get("judgment_reason") or ""
                lines.append(f"{i}. {lot[:45]}  ¥{cost:,.0f}  ({reason[:30]})")
            if len(review_list) > 10:
                lines.append(f"... 他{len(review_list) - 10}件")
            msg = "\n".join(lines)
            if dry_run:
                logger.info(f"[DRY-RUN] REVIEW一括通知:\n{msg}")
                sent["review_sent"] += len(review_list)
            elif _slack_post(msg):
                sent["review_sent"] += len(review_list)

    # ── CEO判断: まとめて1メッセージ
    if ceo_list:
        if len(ceo_list) == 1:
            msg = _format_ceo_message(ceo_list[0])
        else:
            lines = [f"<!channel> 📋 *CEO判断リスト — {len(ceo_list)}件追加*", ""]
            for i, c in enumerate(ceo_list, 1):
                lot    = c.get("lot_title") or c.get("lot_number") or "?"
                cost   = float(c.get("estimated_cost_jpy") or 0)
                reason = c.get("judgment_reason") or ""
                end_t  = c.get("lot_end_time") or c.get("end_date") or ""
                lines.append(f"{i}. {lot[:45]}  ¥{cost:,.0f}")
                lines.append(f"   理由: {reason[:60]}")
                if end_t:
                    lines.append(f"   終了: {end_t}")
                lines.append("")
            lines.append("→ `python run.py overseas-watch --ceo-list` で詳細確認")
            msg = "\n".join(lines)
        if dry_run:
            logger.info(f"[DRY-RUN] CEO判断通知:\n{msg}")
            sent["ceo_sent"] += len(ceo_list)
        elif _slack_post(msg):
            sent["ceo_sent"] += len(ceo_list)

    return sent


# ── メインエントリ ─────────────────────────────────────────────────

def route_and_notify(
    candidate: dict,
    yahoo_3m_count: int = 0,
    dry_run: bool = False,
) -> tuple[str, str]:
    """
    1件の候補レコードについて判定し、Slackに通知して (judgment, reason) を返す。
    candidates_writer.py から呼ばれる想定。

    Args:
        candidate     : overseas_lot スキーマの dict
        yahoo_3m_count: ヤフオク直近3か月一致データ件数（coin_matcher等から取得）
        dry_run       : True の場合は通知しない

    Returns:
        (judgment, judgment_reason)
    """
    c = dict(candidate)
    c["yahoo_3m_count"] = yahoo_3m_count

    judgment, reason = decide_judgment(c)

    if judgment == "OK":
        c["judgment"]        = judgment
        c["judgment_reason"] = reason
        msg = _format_ok_message(c)
        if dry_run:
            logger.info(f"[DRY-RUN] 🟢 OK: {c.get('lot_title', '')[:50]}\n  {reason}")
        else:
            _slack_post(msg)
            logger.info(f"  🟢 OK通知送信: {c.get('lot_title', '')[:50]}")

    elif judgment == "REVIEW":
        c["judgment"]        = judgment
        c["judgment_reason"] = reason
        msg = _format_review_message(c)
        if dry_run:
            logger.info(f"[DRY-RUN] 🟡 REVIEW: {c.get('lot_title', '')[:50]}\n  {reason}")
        else:
            _slack_post(msg)
            logger.info(f"  🟡 REVIEW通知送信: {c.get('lot_title', '')[:50]}")

    elif judgment == "CEO判断":
        c["judgment"]        = judgment
        c["judgment_reason"] = reason
        msg = _format_ceo_message(c)
        if dry_run:
            logger.info(f"[DRY-RUN] 📋 CEO判断: {c.get('lot_title', '')[:50]}\n  {reason}")
        else:
            _slack_post(msg)
            logger.info(f"  📋 CEO判断通知送信: {c.get('lot_title', '')[:50]}")

    else:  # NG
        # 無音: DB保存のみ
        logger.debug(f"  ⛔ NG (無音): {c.get('lot_title', '')[:50]} | {reason}")

    return judgment, reason
