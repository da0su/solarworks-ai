"""seed_users.json の 447 seed を一斉調査.

CEO 5/8 10:25 「フォロワー調査タブ作成・URL を一目瞭然で消費可能に」指示で実装。

調査項目 (各 seed):
1. 自分が既にフォローしているか (フォロー中 / フォローする ボタンで判定)
2. 相手のフォロー数 (彼らが follow している数)
3. 相手のフォロワー数 (彼らに follow されている数)
4. URL = https://room.rakuten.co.jp/{seed}/items
5. 楽天は seed 入手不能なら 404 / フォロー中で url 利用不可 (skip 対象)

Output:
- spreadsheet 06 フォロワー調査 タブに書込
- columns: seed_user / url / my_status / follower_count / following_count / category / has_button / notes
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

import config
from executor.browser_manager import BrowserManager
from logger.logger import setup_logger

logger = setup_logger()

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED_FILE = BOT_DIR / "executor" / "seed_users.json"
SSOT_SPREADSHEET_ID = "1vTWzNZeesXkOFEyNTnufa5K_TZwnhgCh4V6ZtyuHXL0"
GSPREAD_CREDS = REPO_ROOT / "credentials" / "sheets_service_account.json"
SHEET_NAME = "06_フォロワー調査"


def load_all_seeds() -> dict[str, list[str]]:
    """カテゴリ別 + 全 unique."""
    seeds = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    return seeds


def parse_count_str(s: str) -> int:
    """「47K」「6,555」「1.2万」 → int."""
    if not s:
        return 0
    s = s.replace(",", "").strip()
    m = re.match(r"^([\d.]+)\s*([KkMm万千]?)\s*$", s)
    if not m:
        # 数値だけ抽出
        nums = re.findall(r"[\d.]+", s)
        if nums:
            try: return int(float(nums[0]))
            except: return 0
        return 0
    n = float(m.group(1))
    unit = m.group(2)
    if unit in ("K", "k"): n *= 1000
    elif unit in ("M", "m"): n *= 1_000_000
    elif unit == "万": n *= 10000
    elif unit == "千": n *= 1000
    return int(n)


def investigate_seed(bm, seed: str, category: str, already_followed: set | None = None) -> dict:
    """1 seed を調査. already_followed を渡せば overlap (F列) も計算."""
    page = bm.page
    url = f"https://room.rakuten.co.jp/{seed}/items"
    result = {
        "seed_user": seed,
        "category": category,
        "url": url,
        "my_status": "unknown",
        "follower_count": 0,
        "following_count": 0,
        "followed_overlap": 0,  # 2026-05-10 CEO 指示: フォロワーのうち私が follow した数
        "has_button": False,
        "notes": "",
    }
    try:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            if "crashed" in str(e).lower():
                try: page.close()
                except Exception: pass
                page = bm._context.new_page()
                bm._page = page
                page.set_default_timeout(config.ELEMENT_TIMEOUT)
                page.set_default_navigation_timeout(config.PAGE_LOAD_TIMEOUT)
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
            else:
                result["my_status"] = "error"
                result["notes"] = f"goto:{str(e)[:50]}"
                return result
        page.wait_for_timeout(1500)
        # 404?
        title = page.title()
        if "404" in title or "見つかりません" in title:
            result["my_status"] = "404"
            return result
        # button + counts
        info = page.evaluate('''() => {
            const btns = Array.from(document.querySelectorAll('button'));
            let myStatus = 'unknown';
            let hasBtn = false;
            for (const b of btns) {
                const aria = b.getAttribute('aria-label') || '';
                if (aria === 'フォローする') { myStatus = 'not_following'; hasBtn = true; break; }
                if (aria === 'フォロー中') { myStatus = 'following'; hasBtn = true; break; }
                if (aria === 'フォローを外す') { myStatus = 'following'; hasBtn = true; break; }
            }
            // フォロー数 / フォロワー数 抽出 (button text or label):
            // <button>フォロー 1,538</button> or <label>フォロワー 47K</label>
            let followingCount = '';
            let followerCount = '';
            for (const b of btns) {
                const t = (b.textContent || '').trim();
                if (t.match(/^フォロー(?!中|する)\\s*[\\d,KkMm万千.]/) ) {
                    // 'フォロー 1,538' 等の数値
                    const m = t.match(/^フォロー\\s*([\\d,KkMm万千.]+)/);
                    if (m) followingCount = m[1];
                }
                if (t.match(/^フォロワー\\s*[\\d,KkMm万千.]/) ) {
                    const m = t.match(/^フォロワー\\s*([\\d,KkMm万千.]+)/);
                    if (m) followerCount = m[1];
                }
            }
            return { myStatus, hasBtn, followerCount, followingCount, title: document.title };
        }''')
        result["my_status"] = info.get("myStatus", "unknown")
        result["has_button"] = info.get("hasBtn", False)
        result["follower_count"] = parse_count_str(info.get("followerCount", ""))
        result["following_count"] = parse_count_str(info.get("followingCount", ""))
        result["notes"] = info.get("title", "")[:50]

        # 2026-05-10 CEO 指示: F 列 (followed_overlap) 計算
        # modal を開いて follower username を harvest, my already_followed と intersect
        if already_followed is not None and result["follower_count"] > 0:
            try:
                fb = page.locator('button:has-text("フォロワー"):not(:has-text("フォロー中"))').first
                if fb.count() > 0:
                    fb.click(timeout=3000)
                    page.wait_for_timeout(2500)
                    # scroll modal で lazy load 発火 (10 iter ~7s)
                    for _ in range(10):
                        page.evaluate('''() => {
                            const popup = document.querySelector('[class*="popup-container"]');
                            if (popup) popup.querySelectorAll('*').forEach(el => {
                                const cs = window.getComputedStyle(el);
                                if ((cs.overflowY === 'auto' || cs.overflowY === 'scroll') && el.clientHeight > 100) {
                                    el.scrollTop = el.scrollHeight;
                                }
                            });
                        }''')
                        page.wait_for_timeout(700)
                    # extract follower usernames from modal
                    follower_names = page.evaluate('''() => {
                        const popup = document.querySelector('[class*="popup-container"]');
                        if (!popup) return [];
                        const us = new Set();
                        popup.querySelectorAll('a[href^="/room_"]').forEach(a => {
                            const m = a.getAttribute('href').match(/^\\/(room_[a-zA-Z0-9_]+)/);
                            if (m) us.add(m[1]);
                        });
                        return Array.from(us);
                    }''')
                    overlap = sum(1 for n in follower_names if n in already_followed)
                    result["followed_overlap"] = overlap
                    result["notes"] += f" | harvest={len(follower_names)},overlap={overlap}"
            except Exception as e:
                result["notes"] += f" | overlap_err:{str(e)[:30]}"
    except Exception as e:
        result["my_status"] = "error"
        result["notes"] = str(e)[:80]
    return result


def write_to_sheet(rows: list[dict]):
    """gspread で 06_フォロワー調査 タブに書込.

    2026-05-10 CEO 指示で日本語ヘッダー化 + 列順整理:
    A: インフルエンサー名 (seed_user)
    B: カテゴリ
    C: URL
    D: 私のフォロー状況 (フォロー中 / 未フォロー)
    E: フォロワー数 (このインフルエンサーのフォロワー総数)
    F: 私がフォローした被り数 (このフォロワーのうち私が follow した数 / overlap)
    G: 彼らのフォロー数 (このインフルエンサーが他人をフォローしている数)
    H: ボタン有無
    I: 備考
    J: 調査日時
    """
    try:
        import gspread
    except ImportError:
        logger.error("gspread missing")
        return False
    if not GSPREAD_CREDS.exists():
        logger.error(f"creds missing: {GSPREAD_CREDS}")
        return False
    try:
        gc = gspread.service_account(filename=str(GSPREAD_CREDS))
        sh = gc.open_by_key(SSOT_SPREADSHEET_ID)
        try:
            ws = sh.worksheet(SHEET_NAME)
            ws.clear()
        except Exception:
            ws = sh.add_worksheet(title=SHEET_NAME, rows=500, cols=12)
        # Japanese header
        header = [
            "インフルエンサー名", "カテゴリ", "URL",
            "私のフォロー状況", "フォロワー数",
            "私がフォローした被り数",  # F: overlap (new)
            "彼らのフォロー数",         # G: 旧 following_count
            "ボタン有無", "備考", "調査日時",
        ]
        values = [header]
        ts = datetime.now().isoformat(timespec="seconds")
        # Translate my_status to Japanese
        status_jp = {"following": "フォロー中", "not_following": "未フォロー", "error": "エラー", "unknown": "不明"}
        for r in rows:
            values.append([
                r.get("seed_user", ""),
                r.get("category", ""),
                r.get("url", ""),
                status_jp.get(r.get("my_status", "unknown"), r.get("my_status", "")),
                r.get("follower_count", 0),
                r.get("followed_overlap", 0),  # F new: overlap count
                r.get("following_count", 0),    # G: old following_count
                "TRUE" if r.get("has_button") else "FALSE",
                r.get("notes", ""),
                ts,
            ])
        ws.update("A1", values)
        # Format header bold
        try:
            ws.format("A1:J1", {"textFormat": {"bold": True}})
        except Exception:
            pass
        logger.info(f"[OK] wrote {len(rows)} rows to '{SHEET_NAME}' with JP header")
        return True
    except Exception as e:
        logger.error(f"sheet write err: {e}")
        return False


def main():
    seeds_by_cat = load_all_seeds()
    # Build flat unique list with category attribution (first category wins)
    flat = []
    seen = set()
    for cat, lst in seeds_by_cat.items():
        if not isinstance(lst, list): continue
        for s in lst:
            if s in seen: continue
            seen.add(s)
            flat.append((s, cat))
    logger.info(f"investigating {len(flat)} unique seeds")

    # 2026-05-10 CEO 指示: F (overlap) 計算用に follow_history 読込
    already_followed = set()
    try:
        hist = json.loads((BOT_DIR / "data" / "follow_history.json").read_text(encoding="utf-8"))
        for h in hist:
            if isinstance(h, dict):
                if h.get("user_name"): already_followed.add(h["user_name"])
                if h.get("user_id"): already_followed.add(h["user_id"])
        logger.info(f"already_followed loaded: {len(already_followed)} entries")
    except Exception as e:
        logger.warning(f"follow_history load failed: {e}")

    bm = BrowserManager(action="follow")
    bm.start()
    if not bm.check_login_status().get("logged_in"):
        logger.error("not logged in")
        bm.stop()
        return 1

    rows = []
    t0 = time.time()
    for i, (seed, cat) in enumerate(flat, 1):
        r = investigate_seed(bm, seed, cat, already_followed=already_followed)
        rows.append(r)
        if i % 10 == 0 or i == len(flat):
            elapsed = time.time() - t0
            logger.info(f"[{i}/{len(flat)}] elapsed={elapsed:.0f}s status={r['my_status']} overlap={r.get('followed_overlap',0)} {seed}")

    bm.stop()

    # Summary
    n_following = sum(1 for r in rows if r["my_status"] == "following")
    n_not = sum(1 for r in rows if r["my_status"] == "not_following")
    n_404 = sum(1 for r in rows if r["my_status"] == "404")
    n_err = sum(1 for r in rows if r["my_status"] == "error")
    n_unknown = sum(1 for r in rows if r["my_status"] == "unknown")
    logger.info("=" * 60)
    logger.info(f"investigated: {len(rows)}")
    logger.info(f"  following     : {n_following} (already followed)")
    logger.info(f"  not_following : {n_not} (still followable seeds)")
    logger.info(f"  404           : {n_404}")
    logger.info(f"  error         : {n_err}")
    logger.info(f"  unknown       : {n_unknown}")
    logger.info("=" * 60)

    write_to_sheet(rows)

    # Local save
    out_path = config.DATA_DIR / "seed_investigation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"local save: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
