"""comment_edit mode v3: 空 comment 投稿の append 修正 (CEO 5/21 自立対応).

【CEO 厳守事項】
- 過去投稿 5 件以外 絶対触らない (LIMIT 5 in SQL)
- 削除禁止 / 修正禁止 / 既存 textarea 上書き NG
- 追加のみ (focus → End → type)
- ROOM と DB が完全違うなら touch せず log のみ → differs_skip

【Codex 42 & 43 review 反映 (v3)】
- LIMIT 5
- pending=0 は 確定 (success: edited/already_match, fail-定義済: differs_skip/no_room_url) のみ
  transient (captcha/no_button/network/verify_fail/exception) は pending=1 維持で次回 retry
- 二重追記防止 marker: zero-width 文字のみ (ZWSP/ZWJ/ZWNJ で hash bit-encode) → 可視文字混入なし
- read-after-write 厳密検証: (1) save click 直後 URL が edit 抜けたか (2) 公開 page で selector text == db_comment+marker 完全一致 のみ成功
- CAPTCHA iframe + テキスト 2方法
- my/items は batch 初回のみ scrape (rate limit 対策)
- DB schema migration: column 個別 idempotent + WAL + busy_timeout
- is_visible() の timeout 引数 bug 修正 (wait_for で代用)
- TRANSIENT/PERMANENT 分類 を 実コードで使用
- prefix 比較: 両者 NFC+strip した後で行う
- job_success: edited + already_match >= total かつ failed == 0 (differs_skip も成功扱い: CEO 厳守なら触らないのが正解)
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path


# 2026-05-24 EMERGENCY: VM disk full cleanup
# comment_edit_executor は copy /Y で確実に同期されるので、ここで cleanup を仕込む.
# Module load 時に 1 回だけ実行 → 次の watcher pulse 以降は rakuten_room_runner.py 等も同期される.
def _emergency_disk_cleanup_once():
    # 2026-05-24: flag を毎日リセット (24h で 1 回実行) + disk 残量低い時は即時
    flag = Path(r"C:\Users\cyber\AppData\Local\Temp\_emer_cleanup_done")
    try:
        free_mb = shutil.disk_usage("C:\\").free / 1024 / 1024
    except Exception:
        free_mb = 9999
    try:
        if flag.exists() and free_mb > 500:
            # 500 MB 以上 free あれば skip (flag age 関係なく)
            return 0
        if flag.exists() and (time.time() - flag.stat().st_mtime) < 3600:
            # 1h 以内に実行済なら skip (但し 500MB 未満は強制)
            if free_mb > 200:
                return 0
    except Exception:
        return 0
    user_root = Path(os.environ.get("USERPROFILE", r"C:\Users\cyber"))
    targets = [
        Path(os.environ.get("TEMP", r"C:\Windows\Temp")),
        user_root / "AppData" / "Local" / "Temp",
        user_root / "AppData" / "Local" / "pip" / "cache",
        user_root / "AppData" / "Local" / "Microsoft" / "Windows" / "INetCache",
        # 2026-05-24: Chrome profile caches (毎セッション肥大化)
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_post" / "Default" / "Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_post" / "Default" / "Code Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_like" / "Default" / "Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_follow" / "Default" / "Cache",
        user_root / "Desktop" / "rakuten_room_bot" / "data" / "chrome_profile_followback" / "Default" / "Cache",
        # Edge cache (unused but generated)
        user_root / "AppData" / "Local" / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache",
    ]
    cleaned = 0
    for d in targets:
        if not d.exists():
            continue
        try:
            for item in d.iterdir():
                try:
                    if item.is_dir():
                        for f in item.rglob("*"):
                            if f.is_file():
                                try: cleaned += f.stat().st_size
                                except Exception: pass
                        shutil.rmtree(item, ignore_errors=True)
                    elif item.is_file():
                        try: cleaned += item.stat().st_size
                        except Exception: pass
                        try: item.unlink()
                        except Exception: pass
                except Exception:
                    continue
        except Exception:
            continue
    # shared folder ce_*.log の古いもの削除
    try:
        share = Path(r"\\vboxsvr\vm_data")
        if share.exists():
            ce_logs = sorted(share.glob("ce_*.log"), key=lambda p: p.stat().st_mtime)
            for old in ce_logs[:-3]:
                try:
                    cleaned += old.stat().st_size
                    old.unlink()
                except Exception:
                    pass
    except Exception:
        pass
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.touch()
    except Exception:
        pass
    print(f"[disk_cleanup] freed ~{cleaned/1024/1024:.1f} MB")
    return cleaned


# Module load 時に1回だけ実行
try:
    _emergency_disk_cleanup_once()
except Exception as _e:
    print(f"[disk_cleanup] err: {_e}")

VM_DATA_SHARE = Path(r"\\VBOXSVR\vm_data")
HOST_DATA = Path(r"C:\Users\infoa\Documents\solarworks-ai\rakuten-room\bot\data")

MAX_BATCH = 5  # CEO 厳守
# Codex 47 #7: 時間 guard (Task Scheduler 実行時間制限対策)
PER_ITEM_MAX_SEC = 180   # 1 件最長 3 分
BATCH_MAX_SEC = 900      # バッチ全体 最長 15 分

# 一時的失敗 = pending=1 維持 (次回 trigger で retry)
# Codex 48 #6: 実コードで発火しない save_url_not_left_edit を削除. per_item_timeout 追加.
TRANSIENT_FAILURES = {
    "captcha_failed",          # CAPTCHA は人が来れば消える
    "no_edit_button",           # UI 一時不調
    "no_textarea",
    "no_save_button",
    "verify_fail",
    "input_verify_fail",
    "read_after_write_fail",
    "per_item_timeout",         # Task Scheduler 時間枠超過 → 次回 retry
    "save_url_suspect",         # Codex 50 #5: URL 残留 + DOM サイン無し
    "network",
    "navigation_fail",
    "exception",
}
# 確定 / skip = pending=0 (TRANSIENT_FAILURES に該当しない status は全てこちら)
# Codex 45 #6 反映: ドキュメント明示. _update_result の transient 判定で否認集合として機能.


def _resolve_db() -> Path:
    if VM_DATA_SHARE.exists():
        return VM_DATA_SHARE / "room_bot.db"
    return HOST_DATA / "room_bot.db"


def _resolve_log_dir() -> Path:
    base = VM_DATA_SHARE if VM_DATA_SHARE.exists() else HOST_DATA
    return base / "comment_edit_logs"


def _connect_db(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db), timeout=15.0)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass
    return con


def _migrate_schema(db: Path) -> None:
    con = _connect_db(db)
    cols = [r[1] for r in con.execute("PRAGMA table_info(post_queue)").fetchall()]
    for col, ddl in [
        ("pending_comment_edit", "INTEGER DEFAULT 0"),
        ("comment_edit_status", "TEXT"),
        ("comment_edited_at", "TEXT"),
        ("comment_edit_log_path", "TEXT"),
        ("comment_edit_attempts", "INTEGER DEFAULT 0"),
    ]:
        if col not in cols:
            try:
                con.execute(f"ALTER TABLE post_queue ADD COLUMN {col} {ddl}")
            except Exception:
                pass
    con.commit()
    con.close()


def _load_pending(db: Path) -> list[dict]:
    con = _connect_db(db)
    rows = con.execute("""
        SELECT id, queue_date, posted_at, item_code, item_url, title, comment, room_url,
               COALESCE(comment_edit_attempts, 0)
        FROM post_queue
        WHERE pending_comment_edit = 1 AND status = 'posted'
        ORDER BY posted_at DESC
        LIMIT ?
    """, (MAX_BATCH,)).fetchall()
    con.close()
    cols_names = ["id","queue_date","posted_at","item_code","item_url","title",
                  "comment","room_url","attempts"]
    return [dict(zip(cols_names, r)) for r in rows]


def _update_result(db: Path, post_id: int, status: str, log_path: str) -> None:
    """status を保存. status が TRANSIENT_FAILURES に該当する場合は pending=1 維持.

    Codex 49 #5 互換: status が未知の 'save_url_*' で始まる場合も transient 扱い
    (legacy 経路でも pending 維持で再試行).
    """
    transient = any(status == s or status.startswith(s + ":") or status.startswith(s + "_")
                    for s in TRANSIENT_FAILURES)
    if not transient and status.startswith("save_url_"):
        transient = True
    con = _connect_db(db)
    if transient:
        con.execute("""
            UPDATE post_queue
            SET comment_edit_status = ?,
                comment_edited_at = ?,
                comment_edit_log_path = ?,
                comment_edit_attempts = COALESCE(comment_edit_attempts, 0) + 1
            WHERE id = ?
        """, (status, datetime.now().isoformat(timespec="seconds"), log_path, post_id))
    else:
        con.execute("""
            UPDATE post_queue
            SET pending_comment_edit = 0,
                comment_edit_status = ?,
                comment_edited_at = ?,
                comment_edit_log_path = ?,
                comment_edit_attempts = COALESCE(comment_edit_attempts, 0) + 1
            WHERE id = ?
        """, (status, datetime.now().isoformat(timespec="seconds"), log_path, post_id))
    con.commit()
    con.close()


def _backfill_room_url(db: Path, post_id: int, room_url: str) -> None:
    con = _connect_db(db)
    con.execute("UPDATE post_queue SET room_url = ? WHERE id = ?", (room_url, post_id))
    con.commit()
    con.close()


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


# ---- zero-width marker (Codex 43 #1) -------------------------------------
ZW_BITS = ("​", "‌", "‍", "⁠")  # 4文字 = 2 bit each
_ZW_TO_BITS = {c: f"{i:02b}" for i, c in enumerate(ZW_BITS)}


def _marker_for(db_comment: str) -> str:
    """db_comment の SHA256 先頭 24bit を 4 種 zero-width 文字で encode.

    人間にも DOM 上にも 不可視. ASCII 一切混入なし.
    形式: BOUNDARY(⁡) + zw_chars(12 文字) + BOUNDARY(⁡)
    """
    h = hashlib.sha256(_nfc(db_comment).encode("utf-8")).digest()[:3]  # 24bit
    bits = "".join(f"{b:08b}" for b in h)  # 24 char 0/1
    enc = "".join(ZW_BITS[int(bits[i:i+2], 2)] for i in range(0, 24, 2))  # 12 chars
    return "⁡" + enc + "⁡"


def _has_marker(text: str, db_comment: str) -> bool:
    """marker 完全一致. boundary が Rakuten sanitize で落ちた場合は fallback で
    末尾近傍 (末尾 ±50 char 以内) の 12連続 ZW + 内容 bit が DB と一致する場合のみ許容.

    Codex 47 #2 + Codex 50 #6: fallback の偽陽性余地を抑制 (末尾近傍に位置限定).
    """
    if not text:
        return False
    full = _marker_for(db_comment)
    if full in text:
        return True
    # fallback: 末尾近傍 (最後 50 chars) で 12 連続 ZW を探す
    h = hashlib.sha256(_nfc(db_comment).encode("utf-8")).digest()[:3]
    bits = "".join(f"{b:08b}" for b in h)
    inner_enc = "".join(ZW_BITS[int(bits[i:i+2], 2)] for i in range(0, 24, 2))
    tail = text[-50:] if len(text) > 50 else text
    return inner_enc in tail


def _strip_marker(text: str) -> str:
    """marker を取り除いて 表示テキスト相当 を返す.

    対応パターン (Codex 48 #1 + Codex 49 #3):
      (1) ⁡ + 12ZW + ⁡  (boundary 完全形)
      (2) 12 連続 ZW のみ (boundary が sanitize で落ちた fallback)

    単発/少数 ZW はユーザ起因の正当な文字として残す (誤 already_match 防止).
    """
    if not text:
        return ""
    out = []
    i = 0
    n = len(text)
    while i < n:
        # (1) boundary 完全形
        if text[i] == "⁡":
            j = i + 1
            count = 0
            while j < n and text[j] in ZW_BITS and count < 12:
                j += 1
                count += 1
            if count == 12 and j < n and text[j] == "⁡":
                i = j + 1
                continue
        # (2) 連続 12 ZW
        if text[i] in ZW_BITS:
            j = i
            count = 0
            while j < n and text[j] in ZW_BITS and count < 12:
                j += 1
                count += 1
            if count == 12:
                i = j
                continue
        out.append(text[i])
        i += 1
    return "".join(out)


# ---- CAPTCHA detection (Codex 42 #6) -------------------------------------
def _detect_captcha(page) -> tuple[bool, str]:
    try:
        if page.locator(
            'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="captcha"]'
        ).count() > 0:
            return True, "iframe_detected"
    except Exception:
        pass
    try:
        body = (page.text_content("body") or "")[:5000]
        for marker in ("私はロボットではありません", "ロボットでない",
                       "reCAPTCHA", "I'm not a robot", "robot check",
                       "セキュリティ確認"):
            if marker in body:
                return True, f"text:{marker[:20]}"
    except Exception:
        pass
    return False, ""


def _per_item_log(item_id: int, payload: dict) -> Path:
    log_dir = _resolve_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    p = log_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_id{item_id}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return p


# ---- my/items map (Codex 42 #7: batch 内 cache) ---------------------------
def _scrape_my_items_map(page, max_scroll: int = 12) -> dict[str, str]:
    # CEO 5/22 提供: 本来アカウントの ROOM username
    import os as _os
    username = _os.environ.get("MY_ROOM_USERNAME", "room_e05d4d1c1e")
    url = f"https://room.rakuten.co.jp/{username}/items"
    page.goto(url, timeout=60000, wait_until="domcontentloaded")
    time.sleep(3)
    # DIAG: HTML snippet を共有フォルダに dump (1 回のみ)
    try:
        import time as _t
        from pathlib import Path as _P
        diag_path = _P(r"\\vboxsvr\vm_data") / f"my_items_diag_{int(_t.time())}.html"
        diag_path.write_text(page.content()[:50000], encoding="utf-8", errors="replace")
    except Exception:
        pass
    prev_h = 0
    for _ in range(max_scroll):
        page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
        time.sleep(1.3 + random.uniform(0, 0.7))
        cur = page.evaluate("document.body.scrollHeight")
        if cur == prev_h:
            break
        prev_h = cur
    cards = page.evaluate(r"""() => {
        const out = [];
        document.querySelectorAll('a[href*="/items/"]').forEach(a => {
            const href = a.getAttribute('href') || '';
            const m = href.match(/\/items\/(\d+)/);
            if (!m) return;
            const room_url = href.startsWith('http') ? href : ('https://room.rakuten.co.jp' + href);
            let card = a.closest('li, article, div[class*="item"]') || a.parentElement;
            let ext = '';
            if (card) {
                const ea = card.querySelector('a[href*="item.rakuten.co.jp"]');
                if (ea) ext = ea.getAttribute('href') || '';
            }
            out.push({room_url: room_url, ext_url: ext});
        });
        return out;
    }""")
    m: dict[str, str] = {}
    for c in cards:
        ext = (c.get("ext_url") or "").split("?")[0]
        if ext and ext not in m:
            m[ext] = c["room_url"]
    return m


def _resolve_room_url(my_map: dict, item_code: str, item_url: str | None) -> str | None:
    if item_url:
        base = item_url.split("?")[0]
        if base in my_map:
            return my_map[base]
        parts = (item_code or "").split(":")
        if len(parts) >= 3:
            shop_code, item_tail = parts[0], parts[-1]
            for ext, rurl in my_map.items():
                if shop_code in ext and item_tail in ext:
                    return rurl
    return None


# ---- strict verification (Codex 43 #2/#10) -------------------------------
def _verify_after_save_strict(page, room_url: str, db_comment: str,
                                allow_text_only: bool = True) -> tuple[bool, str]:
    """公開 page で 厳密一致のみ成功 (Codex 46 反映).

    成功条件 (両方必須):
      (a) comment selector の text_content/innerHTML 何れかに marker (zero-width) 存在
      (b) text_content を _strip_marker → NFC strip した結果が db_comment と完全一致

    marker 不存 (CDN/cache/非同期反映で古い DOM) ケースは最大 3 回 retry (
    cache-buster QS + 5/10/15s 待機 + reload). 全 retry でも marker 不存なら fail.

    Codex 55 #4: allow_text_only=False の場合 (url_still_edit=True 時) は
    text-only fallback success path を無効化 (虚偽成功防止).
    """
    selectors = [
        '[data-test*="comment"]', '.item-comment', '[class*="ItemComment"]',
        '[class*="item_comment"]', '.kbv2-item-comment', 'p.comment',
        '[class*="comment-body"]',
    ]
    db_nfc = _nfc(db_comment).strip()
    marker = _marker_for(db_comment)

    # Codex 47 #5: 待機 5/10/15 秒で CDN 反映遅延を取りこぼしにくく
    waits = [5, 10, 15]
    last_reason = "unknown"
    diag: list[dict] = []  # Codex 48 #5: 構造化
    # Codex 53 #2/#3: text-only fallback は attempt 単位で集計
    # 1 attempt = 最大 1 カウント. かつ marker がどこにも見えない場合のみカウント.
    attempt_text_only_only_marks: list[dict] = []  # 各 attempt の {body_match, marker_seen}
    for attempt in range(3):
        # Codex 47 #3: cache-buster は既存 QS 保持
        if "?" in room_url:
            target = room_url + "&_ce=" + str(int(time.time()))
        else:
            target = room_url + "?_ce=" + str(int(time.time()))
        try:
            page.goto(target, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_load_state("networkidle", timeout=10000)
            time.sleep(waits[attempt])
        except Exception as e:
            last_reason = f"navigation_fail: {e}"
            continue
        body_text_match = False
        marker_in_text = False
        marker_in_inner = False
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() == 0:
                    continue
                t = loc.text_content(timeout=2000) or ""
                inner = loc.inner_html(timeout=2000) or ""
                room_nfc = _nfc(_strip_marker(t)).strip()
                # Codex 47 #6: text/inner 個別に marker 検出有無を診断
                m_in_t = _has_marker(t, db_comment)
                m_in_i = _has_marker(inner, db_comment)
                marker_present = m_in_t or m_in_i
                if room_nfc == db_nfc and marker_present:
                    return True, f"selector_exact_with_marker:{sel}:attempt{attempt}"
                if room_nfc == db_nfc:
                    body_text_match = True
                if m_in_t:
                    marker_in_text = True
                if m_in_i:
                    marker_in_inner = True
                diag.append({
                    "selector": sel,
                    "body_match": room_nfc == db_nfc,
                    "marker_text": m_in_t,
                    "marker_inner": m_in_i,
                    "t_len": len(t),
                    "inner_len": len(inner),
                    "attempt": attempt,
                })
            except Exception as e:
                diag.append({"selector": sel, "err": str(e)[:80], "attempt": attempt})
                continue
        # Codex 53 #2/#3: attempt 単位の判定 (1 attempt = 最大 1 カウント)
        attempt_text_only_only_marks.append({
            "body_match": body_text_match,
            "marker_seen": marker_in_text or marker_in_inner,
            "attempt": attempt,
        })
        last_reason = (f"body_text_match={body_text_match}"
                        f" marker_text={marker_in_text}"
                        f" marker_inner={marker_in_inner}"
                        f" attempt={attempt}")
    # Codex 53 #2/#3 + Codex 55 #4: text-only fallback は allow_text_only=True 時のみ
    if allow_text_only:
        all_body_match = all(a["body_match"] for a in attempt_text_only_only_marks)
        no_marker_anywhere = not any(a["marker_seen"]
                                       for a in attempt_text_only_only_marks)
        if (len(attempt_text_only_only_marks) == 3
                and all_body_match and no_marker_anywhere):
            return True, ("edited_text_only_no_marker_strict:"
                           f"attempts={attempt_text_only_only_marks}")
    return False, json.dumps({"reason": last_reason, "diag": diag,
                                "attempt_marks": attempt_text_only_only_marks},
                              ensure_ascii=False, default=str)[:2000]


def run_comment_edit(hb=None, log=None) -> dict:
    db = _resolve_db()
    _migrate_schema(db)
    if log:
        log.log(f"[comment_edit] DB: {db}")

    pending = _load_pending(db)
    if log:
        log.log(f"[comment_edit] pending {len(pending)} items (max {MAX_BATCH})")
    if not pending:
        return {"status": "no_pending", "count": 0,
                "job_success": True, "target": MAX_BATCH}

    # VM 内 (ops package 無) と HOST 両対応の import fallback
    try:
        from ops.vm_v6.runner.browser_manager_v6 import BrowserManagerV6
    except ImportError:
        from .browser_manager_v6 import BrowserManagerV6
    # chrome_profile_post が login 切れの場合 follow profile で代替試行
    # (同一アカウントの別 Chrome profile. follow profile は本日 90 件成功 = login 生きてる)
    import os as _os
    _profile_action = _os.environ.get("COMMENT_EDIT_PROFILE", "post")
    bm = BrowserManagerV6(action=_profile_action)
    # DIAG: cookies sqlite 直接読んで rakuten 系 cookies の存在確認
    try:
        import sqlite3 as _sq
        from pathlib import Path as _P
        for sub in ["Default", ""]:
            ckp = bm.profile / sub / "Network" / "Cookies" if sub else bm.profile / "Network" / "Cookies"
            if ckp.exists():
                _c = _sq.connect(str(ckp))
                rows = _c.execute("SELECT host_key, name FROM cookies WHERE host_key LIKE '%rakuten%' LIMIT 20").fetchall()
                _c.close()
                if log:
                    log.log(f"[diag] cookies@{sub or 'root'}: count={len(rows)} sample={rows[:5]}")
                break
        else:
            if log:
                log.log(f"[diag] cookies file NOT FOUND in {bm.profile}")
    except Exception as _ce:
        if log:
            log.log(f"[diag] cookies check err: {_ce}")
    # profile を temp dir に copy
    try:
        import shutil, tempfile
        from pathlib import Path as _P
        src_profile = bm.profile
        if src_profile.exists():
            tmp_root = _P(tempfile.mkdtemp(prefix="ce_profile_"))
            tmp_profile = tmp_root / "chrome_profile"
            shutil.copytree(str(src_profile), str(tmp_profile),
                            dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "SingletonLock", "SingletonSocket", "SingletonCookie",
                                "lockfile", "*.tmp"))
            bm.profile = tmp_profile
            if log:
                log.log(f"[comment_edit] profile copied to temp: {tmp_profile}")
    except Exception as _ce:
        if log:
            log.log(f"[comment_edit] temp profile copy err: {_ce}")
    bm.start()
    if log:
        log.log(f"[comment_edit] profile_action={_profile_action}")
    # login 状態診断 (my ROOM page を開いて URL/title 確認)
    try:
        bm.page.goto("https://room.rakuten.co.jp/my", timeout=30000,
                       wait_until="domcontentloaded")
        time.sleep(2)
        cur_url = bm.page.url
        cur_title = bm.page.title()
        if log:
            log.log(f"[diag] my ROOM url={cur_url[:100]} title={cur_title[:80]}")
        if "login" in cur_url.lower() or "id.rakuten.co.jp" in cur_url.lower():
            if log:
                log.log(f"[diag] LOGIN REDIRECT detected. profile {_profile_action} expired.")
            try:
                bm.stop()
            except Exception:
                pass
            return {"status": "login_expired", "profile": _profile_action,
                    "count": 0, "job_success": False}
    except Exception as e:
        if log:
            log.log(f"[diag] login check err: {e}")

    results = {
        "total": len(pending),
        "target": MAX_BATCH,
        "edited": 0,
        "already_match": 0,
        "differs_skip": 0,
        "failed": 0,
        "per_item": [],
    }
    my_items_map: dict | None = None
    batch_start = time.time()

    try:
        # BrowserManagerV6 の is_logged_in() で OAuth/SSO cookie 確認
        if hasattr(bm, "is_logged_in"):
            logged_in = False
            try:
                logged_in = bm.is_logged_in()
            except Exception as _le:
                if log:
                    log.log(f"[comment_edit] is_logged_in err: {_le}")
            if not logged_in:
                # session/upgrade に redirect なら自動通過試行
                if hasattr(bm, "handle_session_upgrade"):
                    su = bm.handle_session_upgrade()
                    if log:
                        log.log(f"[comment_edit] session_upgrade result: {su}")
                    if su.get("handled"):
                        # 再 check
                        try:
                            logged_in = bm.is_logged_in()
                        except Exception:
                            pass
                if not logged_in:
                    if log:
                        log.log("[comment_edit] not logged in - ABORT")
                    try:
                        bm.stop()
                    except Exception:
                        pass
                    return {"status": "not_logged_in", "count": 0,
                            "job_success": False, "profile": _profile_action}

        for it in pending:
            # Codex 47 #7: バッチ時間 guard
            if time.time() - batch_start > BATCH_MAX_SEC:
                if log:
                    log.log(f"[comment_edit] BATCH_MAX_SEC 超過 → 残 {len(pending) - len(results['per_item'])} 件は次回 trigger")
                break
            item_start = time.time()
            iid = it["id"]
            db_comment = _nfc(it.get("comment") or "").strip()
            room_url = it.get("room_url")
            if log:
                log.log(f"[comment_edit] id={iid} title={(it.get('title') or '')[:40]}")
            if not db_comment:
                # DB の comment が空 → 修正対象でない (恒久 skip だが failed カウントしない)
                results["differs_skip"] += 1
                lp = _per_item_log(iid, {"verdict": "DB_COMMENT_EMPTY"})
                _update_result(db, iid, "db_comment_empty", str(lp))
                results["per_item"].append({"id": iid, "status": "db_empty"})
                continue

            # CAPTCHA pre-check
            captcha_hit, why = _detect_captcha(bm.page)
            if captcha_hit:
                results["failed"] += 1
                lp = _per_item_log(iid, {"verdict": "CAPTCHA_DETECTED", "why": why})
                _update_result(db, iid, "captcha_failed", str(lp))  # transient
                results["per_item"].append({"id": iid, "status": "captcha_failed"})
                continue

            # room_url 解決
            if not room_url or not room_url.startswith("http"):
                # Codex 52 #9: my/items スクレイプ失敗時は transient (再試行可能)
                my_items_scrape_failed = False
                if my_items_map is None:
                    try:
                        my_items_map = _scrape_my_items_map(bm.page)
                        if log:
                            log.log(f"[comment_edit] my/items cards={len(my_items_map)}")
                    except Exception as e:
                        my_items_map = {}
                        my_items_scrape_failed = True
                        if log:
                            log.log(f"[comment_edit] my/items scrape err: {e}")
                room_url = _resolve_room_url(my_items_map, it.get("item_code") or "",
                                              it.get("item_url"))
                if not room_url:
                    results["failed"] += 1
                    lp = _per_item_log(iid, {"verdict": "NO_ROOM_URL",
                                              "item_code": it.get("item_code"),
                                              "item_url": it.get("item_url"),
                                              "my_items_scrape_failed": my_items_scrape_failed,
                                              "my_items_card_count": len(my_items_map or {})})
                    # scrape 失敗で空 map なら transient (再試行可)
                    if my_items_scrape_failed or len(my_items_map or {}) == 0:
                        _update_result(db, iid, "navigation_fail", str(lp))
                        results["per_item"].append({"id": iid, "status": "navigation_fail"})
                    else:
                        _update_result(db, iid, "no_room_url", str(lp))
                        results["per_item"].append({"id": iid, "status": "no_room_url"})
                    continue
                _backfill_room_url(db, iid, room_url)

            log_payload: dict = {
                "id": iid, "room_url": room_url,
                "db_comment_len": len(db_comment),
                "started_at": datetime.now().isoformat(),
            }
            # Codex 45 #3: 二重カウント防止 flag
            failure_recorded = False
            try:
                bm.page.goto(room_url, timeout=30000, wait_until="domcontentloaded")
                bm.page.wait_for_load_state("networkidle", timeout=10000)
                time.sleep(2)

                # 編集 button → wait_for + click (Codex 43 #4 fix)
                edit_clicked = False
                for sel in ['a:has-text("編集")', 'button:has-text("編集")',
                             'a[href*="/edit"]']:
                    try:
                        loc = bm.page.locator(sel).first
                        loc.wait_for(state="visible", timeout=3000)
                        loc.click()
                        edit_clicked = True
                        break
                    except Exception:
                        continue
                if not edit_clicked:
                    results["failed"] += 1
                    failure_recorded = True
                    log_payload["verdict"] = "NO_EDIT_BUTTON"
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, "no_edit_button", str(lp))
                    results["per_item"].append({"id": iid, "status": "no_edit_button"})
                    continue
                # Codex 48 #2: PER_ITEM_MAX_SEC 強制
                if time.time() - item_start > PER_ITEM_MAX_SEC:
                    results["failed"] += 1
                    failure_recorded = True
                    log_payload["verdict"] = "PER_ITEM_TIMEOUT"
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, "per_item_timeout", str(lp))
                    results["per_item"].append({"id": iid, "status": "per_item_timeout"})
                    continue
                time.sleep(3)

                # textarea
                textarea = None
                for sel in ['textarea[name="content"]', 'textarea[name="comment"]', 'textarea']:
                    try:
                        loc = bm.page.locator(sel).first
                        loc.wait_for(state="visible", timeout=3000)
                        textarea = loc
                        break
                    except Exception:
                        continue
                if not textarea:
                    results["failed"] += 1
                    failure_recorded = True
                    log_payload["verdict"] = "NO_TEXTAREA"
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, "no_textarea", str(lp))
                    results["per_item"].append({"id": iid, "status": "no_textarea"})
                    continue

                # 現状取得 (Codex 43 #8 / Codex 44 #3: marker 除去 → NFC → strip した後で比較)
                raw_current = textarea.input_value() or ""
                current_stripped = _nfc(_strip_marker(raw_current)).strip()
                log_payload["current_raw_len"] = len(raw_current)
                log_payload["current_stripped_len"] = len(current_stripped)
                log_payload["current_head"] = current_stripped[:60]

                marker = _marker_for(db_comment)
                # Codex 44 #2: already_marker は marker 存在 AND _strip_marker == db_comment の両方
                if _has_marker(raw_current, db_comment) and current_stripped == db_comment:
                    verdict = "ALREADY_MARKER"
                    skip = True
                    new_text_expected = raw_current
                elif _has_marker(raw_current, db_comment) and current_stripped != db_comment:
                    # marker は あるが本文が違う = 古い marker / 改竄 → CEO 厳守で触らない
                    verdict = "ROOM_DIFFERS_NOT_TOUCHED"
                    new_text_expected = raw_current
                    skip = True
                elif current_stripped == db_comment:
                    verdict = "ALREADY_MATCH"
                    skip = True
                    new_text_expected = raw_current
                elif not current_stripped:
                    verdict = "APPEND_FULL_TO_EMPTY"
                    new_text_expected = db_comment + marker
                    skip = False
                elif db_comment.startswith(current_stripped):
                    verdict = "APPEND_MISSING_SUFFIX"
                    new_text_expected = db_comment + marker
                    skip = False
                else:
                    verdict = "ROOM_DIFFERS_NOT_TOUCHED"
                    new_text_expected = raw_current
                    skip = True

                log_payload["verdict"] = verdict
                log_payload["new_text_expected_len"] = len(new_text_expected)

                if skip:
                    if verdict == "ROOM_DIFFERS_NOT_TOUCHED":
                        results["differs_skip"] += 1
                        log_payload["db_comment_head"] = db_comment[:80]
                        log_payload["room_current_head"] = current_stripped[:80]
                        lp = _per_item_log(iid, log_payload)
                        _update_result(db, iid, "differs_skip", str(lp))
                        results["per_item"].append({"id": iid, "status": "differs_skip"})
                    else:
                        results["already_match"] += 1
                        lp = _per_item_log(iid, log_payload)
                        _update_result(db, iid, verdict.lower(), str(lp))
                        results["per_item"].append({"id": iid, "status": verdict.lower()})
                    continue

                # Codex 50 #3 / Codex 51 #1: append-only 厳守
                # End キーは Windows multi-line textarea で「行末」までしか動かない.
                # setSelectionRange でテキスト末尾に caret 移動してから type.
                if current_stripped:
                    append_text = db_comment[len(current_stripped):] + marker
                else:
                    append_text = db_comment + marker
                textarea.focus()
                caret_at_end = False
                try:
                    caret_info = textarea.evaluate(
                        "el => { el.scrollIntoView({block:'center'}); "
                        "el.setSelectionRange(el.value.length, el.value.length); "
                        "return {start: el.selectionStart, end: el.selectionEnd, "
                        "len: el.value.length}; }"
                    )
                    caret_at_end = (caret_info.get("start") == caret_info.get("len")
                                     and caret_info.get("end") == caret_info.get("len"))
                    log_payload["caret_info"] = caret_info
                except Exception:
                    pass
                if not caret_at_end:
                    # fallback: Ctrl+End
                    try:
                        bm.page.keyboard.press("Control+End")
                    except Exception:
                        pass
                # Codex 52 #3: delay 5→12ms (安定性とのバランス)
                bm.page.keyboard.type(append_text, delay=12)
                time.sleep(1)

                # textarea 入力後検証 (Codex 43 #9: 厳密 = strip_marker 後 NFC で db_comment 完全一致 AND marker 存在)
                in_after_raw = textarea.input_value() or ""
                in_after_stripped = _nfc(_strip_marker(in_after_raw)).strip()
                # Codex 48 #2: marker in raw → _has_marker (boundary 落ち対応)
                if in_after_stripped != db_comment or not _has_marker(in_after_raw, db_comment):
                    results["failed"] += 1
                    failure_recorded = True
                    log_payload["verdict_final"] = "input_verify_fail"
                    log_payload["after_input_len"] = len(in_after_raw)
                    log_payload["after_stripped_len"] = len(in_after_stripped)
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, "input_verify_fail", str(lp))
                    results["per_item"].append({"id": iid, "status": "input_verify_fail"})
                    continue

                # save click (Codex 54 #1: textarea の祖先 form を xpath で厳密特定)
                pre_save_url = bm.page.url
                saved = False
                save_err = ""
                # textarea.locator('xpath=ancestor::form[1]') = 当該 textarea の直近の祖先 form
                form_locator = None
                try:
                    candidate = textarea.locator("xpath=ancestor::form[1]")
                    if candidate.count() > 0:
                        form_locator = candidate
                except Exception:
                    form_locator = None

                save_selectors = [
                    'button:has-text("更新")', 'button:has-text("保存")',
                    'button:has-text("完了")', 'button[type="submit"]',
                    'input[type="submit"]',
                ]
                # Codex 55 #1: 2-stage fallback (form scope → global)
                for stage_name, base in [
                    ("form_scope", form_locator), ("global", None),
                ]:
                    if saved or (stage_name == "form_scope" and base is None):
                        continue
                    for sel in save_selectors:
                        try:
                            if base is not None:
                                loc = base.locator(sel).first
                            else:
                                loc = bm.page.locator(sel).first
                            loc.wait_for(state="visible", timeout=2000)
                            loc.click(no_wait_after=True)
                            try:
                                bm.page.wait_for_load_state("networkidle", timeout=15000)
                            except Exception:
                                pass
                            saved = True
                            log_payload["save_selector_used"] = sel
                            log_payload["save_stage"] = stage_name
                            break
                        except Exception as e:
                            save_err = str(e)[:80]
                            continue
                if not saved:
                    results["failed"] += 1
                    failure_recorded = True
                    log_payload["verdict_final"] = "no_save_button"
                    log_payload["save_err"] = save_err
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, "no_save_button", str(lp))
                    results["per_item"].append({"id": iid, "status": "no_save_button"})
                    continue
                time.sleep(2)

                # URL が edit 系から抜けたか (Codex 47 #1: XHR save UI 対応で warning のみ)
                after_save_url = bm.page.url
                log_payload["pre_save_url"] = pre_save_url
                log_payload["after_save_url"] = after_save_url
                import re as _re
                # Codex 56 #1: /edit 専用に絞る (/post は public URL 衝突回避)
                edit_path_re = _re.compile(r"/(my/items/\d+/)?edit(/|\?|#|$)")
                url_still_edit = bool(edit_path_re.search(after_save_url))
                log_payload["url_still_edit_warning"] = url_still_edit

                # Codex 49 #2 / Codex 50 #5: 保存直後の軽量 DOM サイン観測
                dom_signs = {"has_toast": False, "has_success_text": False,
                             "any_button_disabled": False}
                try:
                    dom_signs = bm.page.evaluate(r"""() => {
                        const out = {};
                        out.has_toast = !!document.querySelector(
                            '[class*="toast"], [class*="notification"], [role="status"], [class*="snackbar"]'
                        );
                        out.has_success_text = /保存しました|更新しました|完了しました/.test(
                            document.body ? document.body.innerText.slice(0, 5000) : ''
                        );
                        const btn = document.querySelector(
                            'button:disabled, button[disabled]'
                        );
                        out.any_button_disabled = !!btn;
                        return out;
                    }""")
                    log_payload["save_dom_signs"] = dom_signs
                except Exception as e:
                    log_payload["save_dom_signs_err"] = str(e)[:80]

                # Codex 50 #5: URL 残留 + 全 DOM サイン無し → save 怪しい → transient
                if (url_still_edit and not dom_signs.get("has_toast")
                        and not dom_signs.get("has_success_text")
                        and not dom_signs.get("any_button_disabled")):
                    results["failed"] += 1
                    failure_recorded = True
                    log_payload["verdict_final"] = "save_url_suspect"
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, "save_url_suspect", str(lp))
                    results["per_item"].append({"id": iid, "status": "save_url_suspect"})
                    continue

                # Codex 48 #2 + Codex 51 #4: PER_ITEM_MAX_SEC 強制 (verify 前)
                # URL 残留 + DOM サイン無しなら save_url_suspect を優先記録
                if time.time() - item_start > PER_ITEM_MAX_SEC:
                    results["failed"] += 1
                    failure_recorded = True
                    if (url_still_edit and not dom_signs.get("has_toast")
                            and not dom_signs.get("has_success_text")
                            and not dom_signs.get("any_button_disabled")):
                        status_v = "save_url_suspect"
                    else:
                        status_v = "per_item_timeout"
                    log_payload["verdict_final"] = f"{status_v}_pre_verify"
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, status_v, str(lp))
                    results["per_item"].append({"id": iid, "status": status_v})
                    continue
                # 公開 page で strict verify
                # Codex 55 #4: URL 残留時は text-only fallback を無効化 (虚偽成功防止)
                ok, why = _verify_after_save_strict(
                    bm.page, room_url, db_comment,
                    allow_text_only=not url_still_edit)
                log_payload["verify_ok"] = ok
                log_payload["verify_reason"] = why
                if ok:
                    results["edited"] += 1
                    # Codex 49 #1: DB には従来形 edited_X を保存 (downstream 互換).
                    # marker/URL warn は log_payload に構造化フィールドで保持.
                    db_status_label = f"edited_{verdict.lower()}"
                    if url_still_edit:
                        log_payload["url_still_edit"] = True
                    if "with_marker" in why:
                        log_payload["marker_verified"] = True
                    # Codex 49 #6: verify 後 timeout check (越過成功を見逃さない)
                    if time.time() - item_start > PER_ITEM_MAX_SEC:
                        log_payload["timeout_overrun"] = True
                    log_payload["verdict_final"] = db_status_label
                    log_payload["finished_at"] = datetime.now().isoformat()
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, db_status_label, str(lp))
                    results["per_item"].append({"id": iid, "status": db_status_label,
                                                  "marker_verified": "with_marker" in why,
                                                  "url_still_edit": url_still_edit})
                else:
                    results["failed"] += 1
                    failure_recorded = True
                    log_payload["verdict_final"] = "read_after_write_fail"
                    lp = _per_item_log(iid, log_payload)
                    _update_result(db, iid, "read_after_write_fail", str(lp))
                    results["per_item"].append({"id": iid,
                                                  "status": "read_after_write_fail"})

                # per-item time check
                if time.time() - item_start > PER_ITEM_MAX_SEC:
                    log_payload["per_item_timeout_warn"] = True
                time.sleep(5 + random.uniform(0, 3))
            except Exception as e:
                # Codex 45 #3: 既に内部で failed カウント済なら 二重カウントしない
                if not failure_recorded:
                    results["failed"] += 1
                log_payload["verdict_final"] = "exception"
                log_payload["error"] = str(e)[:300]
                try:
                    lp = _per_item_log(iid, log_payload)
                except Exception:
                    lp = Path("")
                _update_result(db, iid, f"exception: {str(e)[:200]}", str(lp))
                results["per_item"].append({"id": iid, "status": "exception",
                                              "error": str(e)[:200]})
    finally:
        try:
            bm.stop()
        except Exception:
            pass

    # Codex 49 #4 + Codex 50 #1/#2 + Codex 51 #3/#5:
    # results キー安定化: completed/partial 両方で total=tried + tried 併設.
    # partial でも tried>0 かつ failed==tried (全件失敗) なら job_success=False (偽陰性防止).
    tried = len(results["per_item"])
    success_count = (results["edited"] + results["already_match"]
                      + results["differs_skip"])
    # Codex 52 #1: total は従来意味 (len(pending) = 対象件数) を維持し tried を併設
    results["total"] = len(pending)
    results["tried"] = tried
    results["pending_total"] = len(pending)
    if tried < len(pending):
        results["partial"] = True
        results["remaining"] = len(pending) - tried
        # 全件失敗 partial は偽陰性検知のため job_success=False
        if tried > 0 and results["failed"] == tried:
            results["job_success"] = False
            return {"status": "partial_all_failed", **results}
        results["job_success"] = True
        return {"status": "partial", **results}
    results["job_success"] = (success_count >= tried) and (results["failed"] == 0)
    return {"status": "completed", **results}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    out = run_comment_edit()
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    # partial (正常) は exit 0. partial_all_failed は exit 4 (偽陰性防止).
    if out.get("status") == "partial":
        sys.exit(0)
    if out.get("status") == "partial_all_failed":
        sys.exit(4)
    sys.exit(0 if out.get("job_success") else 4)
