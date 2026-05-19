"""直近 30 件の Rakuten ROOM 投稿 comment 監査 + 省略箇所の編集 (CEO 5/20 指示).

【CEO 指示】
> 「直近 30件分の投稿を見直し、投稿文がはいっていないものは編集していれなおして.
>  削除する必要はない」

【前提】
- chrome_profile_post が本来アカウント (商品 3500/フォロワー 18K) に login 済
- DB の post_queue.comment (status='posted') が full text の正本

【手順】
1. my ROOM items page (https://room.rakuten.co.jp/my/items) を scroll で 30件以上 load
2. 各 item の URL + comment 取得
3. DB と突合:
   - DB の comment と ROOM の comment が **文字数 + 先頭/末尾一致** なら OK
   - 不一致 (省略あり) なら 該当 item の編集 page に遷移し、textarea に DB の comment を fill + save
4. レポート出力 (state/comment_audit_<timestamp>.json)

【使い方】
    python rakuten-room/bot/scripts/update_recent_30_comments.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT.parent))

import config
from executor.browser_manager import BrowserManager


def _fetch_room_items(page, target_count: int = 30) -> list[dict]:
    """my ROOM items page から直近 target_count 件取得.

    各 item dict: {item_id, item_url, room_url (詳細), comment_excerpt}
    """
    page.goto("https://room.rakuten.co.jp/my/items", timeout=60000, wait_until="domcontentloaded")
    time.sleep(3)
    # Lazy load: scroll N 回
    for _ in range(15):
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        time.sleep(1.5)
    items = page.evaluate("""(maxN) => {
        const out = [];
        document.querySelectorAll('a[href*="/items/"]').forEach(el => {
            const href = el.getAttribute('href') || '';
            const m = href.match(/\\/items\\/(\\d+)/);
            if (m && out.length < maxN) {
                out.push({
                    item_id: m[1],
                    room_url: href.startsWith('http') ? href : 'https://room.rakuten.co.jp' + href,
                    excerpt: (el.innerText || '').slice(0, 200).trim(),
                });
            }
        });
        return out;
    }""", target_count)
    # dedupe by item_id
    seen = set()
    unique = []
    for it in items:
        if it["item_id"] not in seen:
            seen.add(it["item_id"])
            unique.append(it)
    return unique[:target_count]


def _fetch_full_comment_for_item(page, room_url: str) -> str:
    """ROOM item 詳細 page から full comment 取得."""
    page.goto(room_url, timeout=60000, wait_until="domcontentloaded")
    time.sleep(2)
    # comment は item 詳細ページの 説明 area にある
    return page.evaluate(r"""() => {
        // 候補: .item-description, .comment, .description, p.comment-body 等
        const candidates = [
            '.item-comment', '.kbv2-item-comment', '.item-description',
            '[class*="comment"]', '[class*="description"]',
            'meta[name="description"]',
        ];
        for (const sel of candidates) {
            const el = document.querySelector(sel);
            if (el) {
                const t = el.tagName === 'META' ? el.getAttribute('content') : el.innerText;
                if (t && t.trim().length > 20) return t.trim();
            }
        }
        // fallback: og:description
        const og = document.querySelector('meta[property="og:description"]');
        return og ? og.getAttribute('content') : '';
    }""")


def _load_db_recent_posted(limit: int = 30) -> list[dict]:
    db = ROOT / "bot" / "data" / "room_bot.db"
    con = sqlite3.connect(str(db))
    rows = con.execute("""
        SELECT id, queue_date, item_code, item_url, title, comment, room_url, posted_at
        FROM post_queue
        WHERE status='posted' AND comment IS NOT NULL AND length(comment) > 0
        ORDER BY id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    con.close()
    cols = ["id","queue_date","item_code","item_url","title","comment","room_url","posted_at"]
    return [dict(zip(cols, r)) for r in rows]


def _compare_comment(db_comment: str, room_comment: str) -> dict:
    """DB comment と ROOM comment を比較し 省略パターン判定."""
    db_len = len(db_comment or "")
    room_len = len(room_comment or "")
    if not room_comment:
        return {"verdict": "EMPTY", "db_len": db_len, "room_len": 0, "delta": -db_len}
    if room_comment.strip() == db_comment.strip():
        return {"verdict": "EXACT_MATCH", "db_len": db_len, "room_len": room_len, "delta": 0}
    # length 一致 + 先頭末尾 一致?
    head = db_comment[:20] == room_comment[:20]
    tail = db_comment[-20:] == room_comment[-20:]
    if db_len == room_len and head and tail:
        return {"verdict": "SAME_LENGTH_DIFF_BODY", "db_len": db_len, "room_len": room_len, "delta": 0, "head_match": head, "tail_match": tail}
    if room_len < db_len * 0.7:
        return {"verdict": "TRUNCATED", "db_len": db_len, "room_len": room_len, "delta": room_len - db_len, "head_match": head, "tail_match": tail}
    return {"verdict": "PARTIAL", "db_len": db_len, "room_len": room_len, "delta": room_len - db_len, "head_match": head, "tail_match": tail}


def _edit_room_item_comment(page, room_url: str, new_comment: str, dry_run: bool = False) -> dict:
    """ROOM item の編集 page に遷移して comment を上書き save.

    Note: 編集 page の URL pattern は room.rakuten.co.jp/edit/<item_id> or
    item 詳細 page の「編集」ボタンから遷移. Rakuten ROOM の現代 UI で要 DOM 確認.
    """
    # まず item 詳細 page を開く
    page.goto(room_url, timeout=60000, wait_until="domcontentloaded")
    time.sleep(2)
    # 「編集」 button or link を探す
    edit_btn = None
    for sel in ['a:has-text("編集")', 'button:has-text("編集")', 'a[href*="/edit"]']:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=3000)
            edit_btn = loc
            break
        except Exception:
            continue
    if not edit_btn:
        return {"status": "no_edit_button", "url": page.url}
    edit_btn.click()
    time.sleep(3)
    # 編集 page で textarea[name='content'] or similar に new_comment を fill
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
        return {"status": "no_textarea", "url": page.url}
    if dry_run:
        return {"status": "dry_run_textarea_found", "url": page.url}
    textarea.fill(new_comment)
    time.sleep(1)
    entered = textarea.input_value()
    if entered != new_comment:
        return {"status": "fill_mismatch", "entered_len": len(entered), "expected_len": len(new_comment)}
    # save button
    for sel in ['button:has-text("更新")', 'button:has-text("保存")', 'button:has-text("完了")', 'button[type="submit"]']:
        try:
            loc = page.locator(sel).first
            if loc.is_visible():
                loc.click()
                time.sleep(3)
                return {"status": "saved", "url": page.url, "comment_len": len(new_comment)}
        except Exception:
            continue
    return {"status": "no_save_button", "url": page.url}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true", help="編集せず 監査のみ")
    args = ap.parse_args()

    # profile health check
    from shared.profile_health import fetch_my_room_fingerprint
    bm = BrowserManager(action="post")
    bm.start()
    try:
        st = bm.check_login_status()
        if not st.get("logged_in"):
            print("ERR: not logged in. CEO の login 後再実行してください.")
            return 1
        fp = fetch_my_room_fingerprint(bm.page)
        print(f"profile fingerprint: items={fp.get('item_count')}, followers={fp.get('follower_count')}")
        if (fp.get("item_count") or 0) < 100:
            print(f"ERR: profile が空アカウント疑い (商品 {fp.get('item_count')} 件). 本来アカウントへ login し直してから再実行.")
            return 2

        # Step 1: ROOM 最新 N 件取得
        print(f"\n=== Step 1: ROOM 最新 {args.limit} 件取得 ===")
        room_items = _fetch_room_items(bm.page, args.limit)
        print(f"取得: {len(room_items)} 件")

        # Step 2: DB 直近 posted 取得
        print(f"\n=== Step 2: DB 直近 posted {args.limit} 件取得 ===")
        db_posts = _load_db_recent_posted(args.limit * 3)  # 余裕持って取得
        print(f"DB: {len(db_posts)} 件")

        # Step 3: 突合
        print(f"\n=== Step 3: 突合 ===")
        audit_log = []
        for ri in room_items:
            # DB の room_url と完全一致 or item_id 一致を探す
            iid = ri["item_id"]
            db_match = next((d for d in db_posts if iid in (d.get("room_url") or "")), None)
            if not db_match:
                audit_log.append({"item_id": iid, "room_url": ri["room_url"], "verdict": "NO_DB_MATCH"})
                continue
            # ROOM の comment 取得
            room_comment = _fetch_full_comment_for_item(bm.page, ri["room_url"])
            cmp = _compare_comment(db_match["comment"], room_comment)
            entry = {
                "item_id": iid, "room_url": ri["room_url"], "db_id": db_match["id"],
                "title": (db_match.get("title") or "")[:60],
                **cmp,
                "db_comment_head": (db_match["comment"] or "")[:40],
                "room_comment_head": (room_comment or "")[:40],
            }
            audit_log.append(entry)
            print(f"  {entry['verdict']:25} item_id={iid} db_id={db_match['id']} db_len={cmp['db_len']:3} room_len={cmp['room_len']:3} {entry['title'][:40]}")

        # Step 4: 省略があるものを編集
        broken = [a for a in audit_log if a.get("verdict") in ("EMPTY", "TRUNCATED", "PARTIAL")]
        print(f"\n=== Step 4: 省略あり {len(broken)} 件 ===")
        for b in broken:
            print(f"  edit: item_id={b['item_id']} verdict={b['verdict']} db_len={b.get('db_len')} room_len={b.get('room_len')}")
            db_match = next((d for d in db_posts if str(b['item_id']) in (d.get("room_url") or "")), None)
            if not db_match:
                continue
            edit_result = _edit_room_item_comment(bm.page, b["room_url"], db_match["comment"], dry_run=args.dry_run)
            b["edit_result"] = edit_result
            print(f"    => {edit_result.get('status')}")

        # Step 5: 結果保存
        out = ROOT.parent / "state" / f"comment_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "audited_at": datetime.now().isoformat(),
            "fingerprint": fp,
            "audit_log": audit_log,
            "broken_count": len(broken),
            "edited_count": sum(1 for b in broken if b.get("edit_result", {}).get("status") == "saved"),
            "dry_run": args.dry_run,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[saved] {out}")
        return 0
    finally:
        bm.stop()


if __name__ == "__main__":
    sys.exit(main())
