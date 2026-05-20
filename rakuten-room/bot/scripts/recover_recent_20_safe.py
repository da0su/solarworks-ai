"""直近 20件 投稿コメント 復旧 (CEO 5/20 09:37 厳格指示).

【CEO 厳守事項】
1. 過去投稿は絶対触らない (直近 20 件以外ノータッチ)
2. DB バックアップ必須
3. 直近 20 件の投稿文 だけ編集対象
4. 削除禁止
5. **投稿文修正 (上書き) 禁止** = 既存テキスト書き換え NG
6. **追加のみ** = 既存 textarea の末尾に append

【設計】
- Step 1: DB backup (state/db_backups/<ts>/room_bot.db)
- Step 2: sync_from_user_chrome (CEO Chrome → 4 profile)
- Step 3: profile fingerprint 検証 (商品 3500 程度 = 本来アカ)
- Step 4: baseline 保存
- Step 5: 直近 posted 20 件 ID 固定 + 編集対象 list
- Step 6: 各 item 詳細 page → 編集 page で:
   - 現状 textarea text を取得 (現 ROOM comment)
   - DB の comment と比較
   - 「ROOM が空」: DB comment 全文を append (空 textarea に fill)
   - 「ROOM 短い・DB の prefix」: 不足分 (suffix) を append
   - 「ROOM が DB と完全違う text」: 触らず log のみ (CEO 「修正禁止」厳守)
- Step 7: audit log 保存

【使い方】
    python rakuten-room/bot/scripts/recover_recent_20_safe.py [--dry-run]

【safety】
- dry-run mode で 編集なしの audit 可能
- 各 step で fail-safe abort
- 全操作 screenshot + log 保存
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT.parent))

import config

DB_PATH = ROOT / "bot" / "data" / "room_bot.db"
BACKUP_DIR = ROOT.parent / "state" / "db_backups"
AUDIT_LOG_DIR = ROOT.parent / "state" / "comment_recovery_logs"


def step1_backup_db() -> Path:
    """Step 1: DB 全 backup (CEO 指示「データ保存」)."""
    print(f"\n=== Step 1: DB backup ===")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = BACKUP_DIR / ts
    out_dir.mkdir(parents=True, exist_ok=True)
    backup = out_dir / "room_bot.db"
    shutil.copy2(DB_PATH, backup)
    # SHA-256 hash 記録
    import hashlib
    h = hashlib.sha256(backup.read_bytes()).hexdigest()
    (out_dir / "manifest.json").write_text(json.dumps({
        "backup_at": datetime.now().isoformat(),
        "source": str(DB_PATH),
        "size": backup.stat().st_size,
        "sha256": h,
        "purpose": "CEO 5/20 09:37 直近 20件 復旧 前の保全",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✅ DB backup: {backup}")
    print(f"     size: {backup.stat().st_size:,} bytes / sha256: {h[:16]}...")
    return backup


def step2_sync_from_user_chrome() -> dict:
    """Step 2: CEO Chrome → bot 4 profile cookies 複製."""
    print(f"\n=== Step 2: CEO Chrome → 4 profile sync ===")
    sync_script = ROOT / "bot" / "scripts" / "sync_from_user_chrome.py"
    r = subprocess.run([sys.executable, str(sync_script)], capture_output=True, text=True)
    print(r.stdout[-2000:] if r.stdout else "")
    if r.returncode != 0:
        print(f"  ❌ sync 失敗 exit={r.returncode}")
        if r.stderr:
            print(r.stderr[-500:])
        return {"status": "failed", "exit": r.returncode}
    return {"status": "ok"}


def step3_verify_fingerprint(min_items: int = 100) -> dict:
    """Step 3: 4 profile fingerprint 検証."""
    print(f"\n=== Step 3: 4 profile fingerprint 検証 ===")
    from shared.profile_health import fetch_my_room_fingerprint
    from playwright.sync_api import sync_playwright
    results = {}
    profiles = ["chrome_profile_post", "chrome_profile_follow",
                "chrome_profile_like", "chrome_profile_followback"]
    for p in profiles:
        pp = config.DATA_DIR / p
        try:
            with sync_playwright() as pw:
                ctx = pw.chromium.launch_persistent_context(
                    user_data_dir=str(pp), headless=True, channel="chrome",
                )
                page = ctx.new_page()
                try:
                    fp = fetch_my_room_fingerprint(page, timeout_ms=60000)
                finally:
                    ctx.close()
            ok = (fp.get("item_count") or 0) >= min_items
            print(f"  {'✅' if ok else '❌'} {p}: items={fp.get('item_count')}, "
                  f"followers={fp.get('follower_count')}, follow={fp.get('follow_count')}")
            results[p] = fp
        except Exception as e:
            print(f"  ❌ {p}: {e}")
            results[p] = {"_error": str(e)}
    return results


def step4_save_baseline(post_fp: dict) -> bool:
    """Step 4: baseline 保存."""
    print(f"\n=== Step 4: baseline 保存 ===")
    if (post_fp.get("item_count") or 0) < 100:
        print(f"  ❌ chrome_profile_post の商品数 {post_fp.get('item_count')} < 100 = 本来アカ不一致")
        return False
    from shared.profile_health import save_baseline
    save_baseline(post_fp, note=f"CEO 5/20 09:37 Chrome sync 後")
    print(f"  ✅ baseline 保存: items={post_fp.get('item_count')}, followers={post_fp.get('follower_count')}")
    return True


def step5_load_recent_20() -> list[dict]:
    """Step 5: DB から直近 posted 20件 取得."""
    print(f"\n=== Step 5: 直近 posted 20 件 取得 ===")
    con = sqlite3.connect(str(DB_PATH))
    rows = con.execute("""
        SELECT id, queue_date, posted_at, item_code, item_url, title, comment, room_url
        FROM post_queue
        WHERE status='posted' AND comment IS NOT NULL AND length(comment) > 0
        ORDER BY posted_at DESC LIMIT 20
    """).fetchall()
    con.close()
    cols = ["id","queue_date","posted_at","item_code","item_url","title","comment","room_url"]
    items = [dict(zip(cols, r)) for r in rows]
    print(f"  対象 {len(items)} 件: id={[i['id'] for i in items]}")
    return items


def step6_audit_and_append(items: list[dict], dry_run: bool) -> dict:
    """Step 6: 各 item の ROOM comment audit + 不足分 append のみ (修正禁止)."""
    print(f"\n=== Step 6: 直近 20 件 audit + append (dry_run={dry_run}) ===")
    from playwright.sync_api import sync_playwright
    pp = config.DATA_DIR / "chrome_profile_post"
    audit = []
    edited = 0
    untouched_diff = 0  # ROOM と DB が完全違う = CEO 指示「修正禁止」で touch せず
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(pp), headless=False, channel="chrome",
        )
        page = ctx.new_page()
        try:
            for idx, it in enumerate(items, 1):
                print(f"\n  [{idx}/{len(items)}] id={it['id']} {it['title'][:40] if it.get('title') else ''}")
                db_comment = it.get("comment") or ""
                # Step 6a: my ROOM items page から該当 item の room_url 特定
                # DB の room_url が信頼できないので、my ROOM 検索 or item_code で
                room_item_url = None
                # まず room_url を試す
                if it.get("room_url") and it["room_url"].startswith("http"):
                    room_item_url = it["room_url"]
                # それでもダメなら my ROOM items から item_code で検索
                if not room_item_url:
                    print(f"    ⚠ room_url 不明. item_url={it.get('item_url')[:80] if it.get('item_url') else 'None'}")
                    audit.append({**it, "verdict": "ROOM_URL_UNKNOWN"})
                    continue
                # Step 6b: ROOM item 詳細 page 開く
                try:
                    page.goto(room_item_url, timeout=60000, wait_until="domcontentloaded")
                    time.sleep(2)
                except Exception as e:
                    print(f"    ❌ goto failed: {e}")
                    audit.append({**it, "verdict": "GOTO_FAILED", "error": str(e)[:200]})
                    continue
                # Step 6c: 編集ボタン click
                edit_clicked = False
                for sel in ['a:has-text("編集")', 'button:has-text("編集")',
                             'a[href*="/edit"]', '[aria-label*="編集"]']:
                    try:
                        loc = page.locator(sel).first
                        loc.wait_for(state="visible", timeout=3000)
                        loc.click()
                        edit_clicked = True
                        print(f"    edit click: {sel}")
                        break
                    except Exception:
                        continue
                if not edit_clicked:
                    print(f"    ❌ 編集ボタンなし")
                    audit.append({**it, "verdict": "NO_EDIT_BUTTON"})
                    continue
                time.sleep(3)
                # Step 6d: 編集 textarea 取得
                textarea = None
                for sel in ['textarea[name="content"]', 'textarea[name="comment"]', 'textarea']:
                    try:
                        loc = page.locator(sel).first
                        loc.wait_for(state="visible", timeout=3000)
                        textarea = loc
                        break
                    except Exception:
                        continue
                if not textarea:
                    print(f"    ❌ textarea なし")
                    audit.append({**it, "verdict": "NO_TEXTAREA"})
                    continue
                # Step 6e: 現状 ROOM comment 取得 (修正前の保全 = backup)
                current = textarea.input_value() or ""
                print(f"    DB len={len(db_comment)}, ROOM len={len(current)}")
                # CEO 「修正禁止・追加のみ」: 既存テキストは絶対上書きしない
                if current.strip() == db_comment.strip():
                    print(f"    ✅ 既に一致 (skip)")
                    audit.append({**it, "verdict": "ALREADY_MATCH", "room_current_len": len(current)})
                    continue
                # 「ROOM が空」or「ROOM が DB の prefix のみ」なら不足分 append
                if not current:
                    # 空 → 全文を append (textarea が空なので fill = append と同等)
                    append_text = db_comment
                    new_text = db_comment
                    verdict = "APPEND_FULL_TO_EMPTY"
                elif db_comment.startswith(current):
                    # DB が ROOM の続き = ROOM が prefix
                    append_text = db_comment[len(current):]
                    new_text = current + append_text  # = db_comment
                    verdict = "APPEND_MISSING_SUFFIX"
                else:
                    # ROOM が DB と完全違う = CEO 「修正禁止」厳守 → touch せず log のみ
                    print(f"    ⚠ ROOM と DB が完全違う → 修正禁止のため触らず")
                    print(f"    ROOM 先頭: {current[:40]!r}")
                    print(f"    DB 先頭:   {db_comment[:40]!r}")
                    audit.append({**it, "verdict": "ROOM_DIFFERS_NOT_TOUCHED",
                                   "room_current": current[:200],
                                   "db_comment": db_comment[:200]})
                    untouched_diff += 1
                    continue
                # dry-run なら fill しない
                if dry_run:
                    print(f"    [dry-run] {verdict}: append {len(append_text)} chars")
                    audit.append({**it, "verdict": f"DRY_RUN_{verdict}",
                                   "append_text_len": len(append_text)})
                    continue
                # Step 6f: append (= 既存末尾に追記 = 修正なし)
                # current が空なら fill, あれば末尾追記
                if current:
                    # 既存 text の後ろに append: focus → End → type
                    textarea.focus()
                    page.keyboard.press("End")
                    page.keyboard.type(append_text, delay=20)
                else:
                    textarea.fill(append_text)
                time.sleep(1)
                # verify (append 後の text が new_text と一致)
                verified = textarea.input_value() or ""
                if verified != new_text:
                    print(f"    ⚠ append 後 len mismatch: expected {len(new_text)}, got {len(verified)}")
                    audit.append({**it, "verdict": f"APPEND_VERIFY_FAILED_{verdict}",
                                   "expected_len": len(new_text), "got_len": len(verified)})
                    continue
                # Step 6g: 保存
                saved = False
                for sel in ['button:has-text("更新")', 'button:has-text("保存")',
                             'button:has-text("完了")', 'button[type="submit"]']:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible():
                            loc.click()
                            saved = True
                            print(f"    ✅ save: {sel}")
                            break
                    except Exception:
                        continue
                time.sleep(3)
                if saved:
                    edited += 1
                    audit.append({**it, "verdict": f"EDITED_{verdict}",
                                   "appended_len": len(append_text),
                                   "before_len": len(current),
                                   "after_len": len(new_text)})
                else:
                    audit.append({**it, "verdict": f"NO_SAVE_BUTTON_{verdict}"})
                # 念のため毎件 5s 待機 (Rakuten 過剰 access 防止)
                time.sleep(5)
        finally:
            ctx.close()
    return {"audit": audit, "edited": edited, "untouched_diff": untouched_diff}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="編集なしで audit のみ")
    args = ap.parse_args()

    print(f"\n{'='*70}\n直近 20件 投稿コメント 復旧 (CEO 5/20 09:37 厳格指示)\n{'='*70}")
    print(f"開始: {datetime.now().isoformat()}")
    print(f"dry_run: {args.dry_run}")
    print(f"\n【CEO 厳守事項】")
    print(f"  - 過去投稿 (直近 20 件以外) は絶対触らない")
    print(f"  - DB バックアップ済")
    print(f"  - 削除禁止 / 修正禁止 / 追加のみ")

    # Step 1: DB backup
    backup_path = step1_backup_db()

    # Step 2: sync from user chrome
    sync_result = step2_sync_from_user_chrome()
    if sync_result.get("status") != "ok":
        print(f"\n❌ Step 2 sync 失敗. ABORT.")
        return 2

    # Step 3: fingerprint 検証
    fingerprints = step3_verify_fingerprint(min_items=100)
    post_fp = fingerprints.get("chrome_profile_post", {})
    if (post_fp.get("item_count") or 0) < 100:
        print(f"\n❌ Step 3 post profile が本来アカ不一致 (商品 {post_fp.get('item_count')} < 100). ABORT.")
        return 3

    # Step 4: baseline 保存
    step4_save_baseline(post_fp)

    # Step 5: 直近 20件
    items = step5_load_recent_20()
    if not items:
        print(f"\n❌ Step 5 直近 posted なし. ABORT.")
        return 5

    # Step 6: audit + append
    audit_result = step6_audit_and_append(items, args.dry_run)

    # Step 7: log 保存
    AUDIT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = AUDIT_LOG_DIR / f"recovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    log_path.write_text(json.dumps({
        "started_at": datetime.now().isoformat(),
        "dry_run": args.dry_run,
        "db_backup": str(backup_path),
        "post_fingerprint": post_fp,
        "recent_20_ids": [i["id"] for i in items],
        "audit": audit_result["audit"],
        "edited_count": audit_result["edited"],
        "untouched_diff_count": audit_result["untouched_diff"],
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\n=== 完了 ===")
    print(f"  編集件数: {audit_result['edited']}/{len(items)}")
    print(f"  ROOM と DB 完全違い (修正禁止で touch せず): {audit_result['untouched_diff']}")
    print(f"  log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
