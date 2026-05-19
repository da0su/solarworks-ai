"""Persona NG queue 隔離スクリプト (CEO 5/20 02:30 緊急止血).

【目的】 既存 post_queue で status='queued' の item を強化版 item_auditor で
再監査し、persona NG だった item を status='persona_quarantine' に隔離する.

【背景】 5/18 以降 メンズベルト/介護用 etc 19件が queue に混入 (CEO 確認済).
audit_persona() 強化 (NFKC + 全半角 + shop_name/description 評価 +
masculine context 検出) 後にこれら隔離が必要.

【usage】
    python rakuten-room/bot/scripts/quarantine_persona_ng.py            # 実行 (本番)
    python rakuten-room/bot/scripts/quarantine_persona_ng.py --dry-run  # 確認のみ

【exit code】
    0 = OK
    1 = error
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

import config
from planner.item_auditor import _persona_check, _normalize_for_persona  # noqa: F401
from logger.logger import setup_logger

logger = setup_logger()

DB = config.DATA_DIR / "room_bot.db"
QUARANTINE_LOG = config.DATA_DIR / "audit" / "persona_quarantine_log.jsonl"


def reaudit_and_quarantine(dry_run: bool = False) -> dict:
    """queued 全 item を再監査し persona NG を 'persona_quarantine' に移動.

    Returns:
        {"checked": N, "quarantined": M, "by_reason": {...}}
    """
    if not DB.exists():
        logger.error(f"DB not found: {DB}")
        return {"_error": "db not found"}

    # Codex 30回目 #7 fix: DB connection は finally で必ず close.
    con = sqlite3.connect(DB, timeout=10)
    con.row_factory = sqlite3.Row
    by_reason: dict[str, int] = {}
    quarantine_ids: list[int] = []
    quarantine_log: list[dict] = []
    try:
        rows = con.execute(
            "SELECT id, item_code, title, comment, genre, item_url "
            "FROM post_queue WHERE status='queued'"
        ).fetchall()

        for r in rows:
            title = r["title"] or ""
            comment = r["comment"] or ""
            # 強化版 _persona_check (NFKC + 全半角 + masculine context)
            # shop_name / description は queue に保存されていない → title + comment のみ
            status, reason = _persona_check(title, comment)
            if status == "fail":
                quarantine_ids.append(r["id"])
                short_title = title[:80]
                quarantine_log.append({
                    "id": r["id"],
                    "item_code": r["item_code"],
                    "title": short_title,
                    "genre": r["genre"],
                    "reason": reason,
                    "url": r["item_url"],
                    "detected_at": datetime.now().isoformat(timespec="seconds"),
                })
                # reason category 集計 (NG kw or context label を抽出)
                if "'" in reason:
                    parts = reason.split("'")
                    key = parts[1] if len(parts) >= 2 else reason[:30]
                else:
                    key = reason[:30]
                by_reason[key] = by_reason.get(key, 0) + 1

        logger.info(f"queued total: {len(rows)} / persona NG (quarantine 対象): {len(quarantine_ids)}")
        for entry in quarantine_log[:30]:
            logger.info(f"  id={entry['id']} reason={entry['reason']!r} title={entry['title']}")

        if dry_run:
            logger.info("[DRY RUN] 実 update せず終了")
            return {
                "checked": len(rows),
                "quarantined": len(quarantine_ids),
                "by_reason": by_reason,
                "dry_run": True,
            }

        # 実 update (atomic transaction)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            con.execute("BEGIN")
            for qid in quarantine_ids:
                con.execute(
                    "UPDATE post_queue SET status='persona_quarantine', "
                    "result_message=?, updated_at=? WHERE id=? AND status='queued'",
                    (f"persona NG (CEO 5/20 quarantine)", now, qid),
                )
            con.commit()
        except Exception as e:
            con.rollback()
            logger.error(f"update failed: {e}")
            return {"_error": str(e)}
    finally:
        try:
            con.close()
        except Exception:
            pass

    # log書込
    QUARANTINE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(QUARANTINE_LOG, "a", encoding="utf-8") as f:
        for entry in quarantine_log:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info(f"quarantine log saved: {QUARANTINE_LOG} (+{len(quarantine_log)} entries)")

    return {
        "checked": len(rows),
        "quarantined": len(quarantine_ids),
        "by_reason": by_reason,
        "dry_run": False,
    }


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="確認のみ (update しない)")
    args = ap.parse_args()
    r = reaudit_and_quarantine(dry_run=args.dry_run)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if "_error" not in r else 1


if __name__ == "__main__":
    sys.exit(main())
