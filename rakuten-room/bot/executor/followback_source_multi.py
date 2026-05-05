#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOLLOWBACK source multi — 複数ソースからフォローバック候補を集約

Phase 4c (2026-04-23): マーケ指示「source_empty で終わらせず、source多角化して
3件から件数を伸ばす」への対応。

Sources:
  S1. /{my_id}/followers       自profileの followers ページ(/my/followersより多く取れる)
  S2. 2-hop 展開               S1で得た follower の profile を訪れ、
                               その followers を候補として収集(friends-of-friends)
  S3. /my/follower             単数形 fallback (ROOM 版違い)
  S4. followback_queue pending 既存プールを status=pending 維持

2026-04-23 実測:
  - /my/followers = 3件しか取れない (SPA virtual scroll が stuck)
  - /{my_id}/followers = 12件取得可 (dom-visible anchors全量取れる)
  - 2-hop: 12 × ~50 = 最大600候補 (重複除外後は200-400想定)

すべてのソースを集約→ follow_log.success で重複除外 →
followback_queue に INSERT (IntegrityError は skip)。

Usage:
    python -m rakuten-room.bot.executor.followback_source_multi --limit 200
    python -m rakuten-room.bot.executor.followback_source_multi --limit 50 --headless
    python -m rakuten-room.bot.executor.followback_source_multi --dry-run

Stop reasons:
  - ok                     : 1ソース以上でカウント確保
  - not_logged_in          : 全ソースでログイン redirect
  - all_sources_empty      : 候補0件
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                    errors="replace", line_buffering=True)

from playwright.sync_api import sync_playwright

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # type: ignore

# 2026-05-05 Phase A-2: profile 分離。followback 機能用 profile を使用
CHROME_PROFILE = config.get_chrome_profile("followback")
CHROME_EXE = getattr(config, "CHROME_EXECUTABLE_PATH", None)
DB_PATH_V5 = BOT_DIR / "data" / "room_bot_v5.db"

UID_RE = re.compile(r'^room_[0-9a-f]{8,}$')
MY_ACCOUNT_ID = getattr(config, "ROOM_ID", "")


def _dbg_shot(page, tag: str):
    try:
        shot_dir = BOT_DIR / "data" / "debug"
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot = shot_dir / f"fb_multi_{tag}_{datetime.now().strftime('%H%M%S')}.png"
        page.screenshot(path=str(shot), full_page=False)
        print(f"[screenshot] {tag} -> {shot}", flush=True)
    except Exception as e:
        print(f"[screenshot_err] {tag} {e}", flush=True)


def _is_login_redirect(url: str) -> bool:
    return ("login.account.rakuten.com" in url) or ("grp01.id" in url)


def _extract_uid_from_href(href: str) -> str | None:
    if "/room_" not in href:
        return None
    tail = href.split("/room_", 1)[1].split("/")[0].split("?")[0]
    cand = "room_" + tail
    return cand if UID_RE.match(cand) else None


def collect_via_api_intercept(page, limit: int, url: str,
                               source_tag: str = "api",
                               is_following_us: bool = True) -> list[dict]:
    """
    APIインターセプト方式でフォロワーを全件取得 (CEO指示 2026-05-01)

    スクロールは SPA の仮想リストに依存するため根本的に不安定。
    代わりに Playwright の network intercept で:
    1. /my/followers を開きながら XHR/fetch を全捕捉
    2. followers を返す JSON エンドポイントを特定
    3. そのエンドポイントを page 上の fetch で直接呼び出し、
       ページネーションを exhausted するまでループ
    4. DOM スクロールは fallback としてのみ使用

    対応 API パターン:
      - /api/v*/follow*/follower*  (REST)
      - /api/*user*/follower*
      - ?page=N, ?offset=N, ?cursor=XXX 等のページネーション
    """
    import json as _json
    results: list[dict] = []
    seen: set[str] = set()
    captured_apis: list[dict] = []

    # --- Phase 1: intercept to discover API endpoint ---
    def handle_response(response):
        try:
            url_r = response.url
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            # Only care about follower-related APIs
            if not any(k in url_r for k in ["follower", "follow", "user", "room"]):
                return
            # Skip tiny responses
            body = response.body()
            if len(body) < 50:
                return
            data = _json.loads(body)
            # Look for arrays of user objects
            if isinstance(data, list) and len(data) > 0:
                captured_apis.append({"url": url_r, "type": "array", "count": len(data), "sample": data[0]})
            elif isinstance(data, dict):
                # Check common array keys
                for key in ("followers", "users", "items", "data", "results", "list"):
                    if key in data and isinstance(data[key], list) and len(data[key]) > 0:
                        captured_apis.append({"url": url_r, "type": f"dict.{key}", "count": len(data[key]), "sample": data[key][0], "total": data.get("total", data.get("count", "?"))})
                        break
        except Exception:
            pass

    page.on("response", handle_response)

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        print(f"[{source_tag}_goto_err] {e}", flush=True)
        page.remove_listener("response", handle_response)
        return []

    try:
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    time.sleep(3)
    page.remove_listener("response", handle_response)

    if _is_login_redirect(page.url):
        print(f"[not_logged_in] {source_tag}", flush=True)
        return []

    print(f"[{source_tag}] captured APIs: {len(captured_apis)}", flush=True)
    for a in captured_apis:
        print(f"  API: {a['url'][:100]} type={a['type']} count={a['count']} total={a.get('total','?')}", flush=True)

    # --- Phase 2: paginate discovered API endpoint ---
    api_used = False
    for api_info in captured_apis:
        api_url = api_info["url"]
        sample = api_info.get("sample", {})

        # Determine user_id field from sample
        uid_field = None
        for f in ("room_id", "user_id", "id", "userId", "account_id"):
            if f in sample:
                uid_field = f
                break
        name_field = None
        for f in ("name", "username", "display_name", "nick_name", "nickName", "room_name"):
            if f in sample:
                name_field = f
                break
        if not uid_field:
            continue  # Can't extract user IDs from this API

        print(f"[{source_tag}] using API: uid_field={uid_field} name_field={name_field}", flush=True)
        api_used = True

        # Paginate: try page=1,2,3... and offset=0,N,2N...
        import urllib.parse as _up
        parsed = _up.urlparse(api_url)
        params = dict(_up.parse_qsl(parsed.query))

        # Detect pagination style
        has_page = "page" in params
        has_offset = "offset" in params
        has_cursor = "cursor" in params or "next_cursor" in params

        page_num = 1
        offset = 0
        per_page = api_info.get("count", 20)

        for _ in range(500):  # max 500 pages = exhaustive
            # Build paginated URL
            new_params = dict(params)
            if has_page:
                new_params["page"] = str(page_num)
            elif has_offset:
                new_params["offset"] = str(offset)
            else:
                new_params["page"] = str(page_num)  # try page param by default

            new_url = _up.urlunparse(parsed._replace(query=_up.urlencode(new_params)))

            try:
                resp_data = page.evaluate(f"""
                    async () => {{
                        const r = await fetch({_json.dumps(new_url)}, {{
                            credentials: 'include',
                            headers: {{'Accept': 'application/json'}}
                        }});
                        if (!r.ok) return null;
                        return await r.json();
                    }}
                """)
            except Exception as e:
                print(f"[{source_tag}] fetch err page={page_num}: {e}", flush=True)
                break

            if not resp_data:
                break

            # Extract items
            items = []
            if isinstance(resp_data, list):
                items = resp_data
            elif isinstance(resp_data, dict):
                for key in ("followers", "users", "items", "data", "results", "list"):
                    if key in resp_data and isinstance(resp_data[key], list):
                        items = resp_data[key]
                        break

            if not items:
                break

            added = 0
            for item in items:
                raw_uid = str(item.get(uid_field, "")).strip()
                if not raw_uid:
                    continue
                # Normalize: ensure room_ prefix
                if not raw_uid.startswith("room_"):
                    raw_uid = "room_" + raw_uid
                if not UID_RE.match(raw_uid):
                    # Maybe uid is just the hex suffix
                    cand = "room_" + raw_uid.replace("room_", "")
                    if UID_RE.match(cand):
                        raw_uid = cand
                    else:
                        continue
                uid = raw_uid
                if uid == MY_ACCOUNT_ID or uid in seen:
                    continue
                name = str(item.get(name_field, uid))[:60] if name_field else uid
                results.append({
                    "user_id": uid,
                    "username": name,
                    "is_following_us": is_following_us,
                    "we_are_following": False,
                    "previously_unfollowed": False,
                    "is_fresh": True,
                    "source": source_tag + "_api",
                })
                seen.add(uid)
                added += 1
                if len(results) >= limit:
                    break

            print(f"[{source_tag}] page={page_num} items={len(items)} added={added} total={len(results)}", flush=True)

            if added == 0 or len(items) < per_page or len(results) >= limit:
                break

            page_num += 1
            offset += len(items)
            time.sleep(0.3)  # gentle

        if results:
            print(f"[{source_tag}] API method success: {len(results)} followers found", flush=True)
            return results

    # --- Phase 3: DOM scroll fallback (if API not found) ---
    if not api_used or not results:
        print(f"[{source_tag}] API method found no results, falling back to DOM scroll", flush=True)
        return collect_from_followers_scroll(page, limit, source_tag, is_following_us)

    return results


def collect_from_followers(page, limit: int, url: str,
                            source_tag: str = "feed",
                            is_following_us: bool = True) -> list[dict]:
    """エントリポイント: APIインターセプト → DOM scrollフォールバック"""
    return collect_via_api_intercept(page, limit, url, source_tag, is_following_us)


def collect_from_followers_scroll(page, limit: int,
                                   source_tag: str = "scroll",
                                   is_following_us: bool = True) -> list[dict]:
    """DOM scrollベースのフォールバック収集 (100回スクロール)"""
    results: list[dict] = []
    seen: set[str] = set()

    # Aggressive scroll — DOM-visible anchors only
    prev_uniq = 0
    stuck = 0
    for i in range(300):  # 300 iter * 2500px = 最大750,000px スクロール
        try:
            anchors = page.query_selector_all('a[href*="/room_"]')
            for a in anchors:
                try:
                    href = (a.get_attribute("href") or "").strip()
                except Exception:
                    continue
                uid = _extract_uid_from_href(href)
                if not uid or uid == MY_ACCOUNT_ID or uid in seen:
                    continue
                try:
                    txt = (a.inner_text() or "").strip() or uid
                except Exception:
                    txt = uid
                results.append({
                    "user_id": uid,
                    "username": txt[:60],
                    "is_following_us": is_following_us,
                    "we_are_following": False,
                    "previously_unfollowed": False,
                    "is_fresh": True,
                    "source": source_tag,
                })
                seen.add(uid)
                if len(results) >= limit:
                    return results
            cur_uniq = len(results)
            if cur_uniq == prev_uniq:
                stuck += 1
                try:
                    page.keyboard.press("End")
                    time.sleep(0.8)
                except Exception:
                    pass
                if stuck >= 2 and anchors:
                    try:
                        anchors[-1].scroll_into_view_if_needed(timeout=2000)
                        time.sleep(0.8)
                    except Exception:
                        pass
                # もっと見る / 次へ ボタンを探す
                if stuck >= 3:
                    try:
                        more = page.query_selector(
                            'a:has-text("もっと見る"), button:has-text("もっと見る"), '
                            'a:has-text("次へ"), button:has-text("次へ"), '
                            'a:has-text("more"), button:has-text("load more")'
                        )
                        if more and more.is_visible():
                            more.click()
                            time.sleep(2)
                            stuck = 0
                            continue
                    except Exception:
                        pass
                if stuck >= 100:
                    print(f"[{source_tag}_done] uniq={cur_uniq} stopped iter={i}", flush=True)
                    break
            else:
                stuck = 0
            prev_uniq = cur_uniq
            page.evaluate("window.scrollBy(0, 2500)")
            page.evaluate("""
                const cs = document.querySelectorAll('[class*="scroll"],[class*="list"],[role="feed"]');
                cs.forEach(c => { if (c.scrollHeight > c.clientHeight) c.scrollTop = c.scrollHeight; });
            """)
            time.sleep(1.0)
        except Exception as e:
            print(f"[{source_tag}_scroll_err] iter={i} {e}", flush=True)
            break
    return results


def collect_from_notifications(page, limit: int) -> list[dict]:
    """S2: /my/notifications から「〇〇さんがあなたをフォローしました」を抽出

    通知ページは follow 通知の鮮度が高く、直近で follow してきた
    ユーザーを時系列で取得できる。SPA 制約は feed 同等。
    """
    results: list[dict] = []
    seen: set[str] = set()

    candidate_urls = [
        "https://room.rakuten.co.jp/my/notifications",
        "https://room.rakuten.co.jp/my/notification",
    ]
    for url in candidate_urls:
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            break
        except Exception as e:
            print(f"[notif_goto_err] {url}: {e}", flush=True)
            continue
    else:
        return []

    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    time.sleep(5)
    print(f"[notif_landed] url={page.url[:120]}", flush=True)
    _dbg_shot(page, "notif")

    if _is_login_redirect(page.url):
        print("[not_logged_in] notif", flush=True)
        return []

    # Notification items: Look for anchors that include follow-related text
    # or container blocks having "フォロー" keyword.
    last_h = 0
    stuck = 0
    for i in range(20):
        try:
            # Cast wide: all anchors pointing to a user room in the notifications list
            anchors = page.query_selector_all('a[href*="/room_"]')
            for a in anchors:
                href = (a.get_attribute("href") or "").strip()
                uid = _extract_uid_from_href(href)
                if not uid or uid == MY_ACCOUNT_ID or uid in seen:
                    continue
                # Get surrounding text to check for follow-related notification
                try:
                    parent = a.evaluate_handle("el => el.closest('li,div,article') || el.parentElement")
                    ptxt = parent.evaluate("el => el ? el.innerText : ''") if parent else ""
                except Exception:
                    ptxt = ""
                is_follow_notif = ("フォロー" in ptxt) or ("follow" in ptxt.lower())
                # Even if not explicitly a follow notification, include — these
                # are users who interacted with us (liked, commented), which is
                # also a good followback signal.
                txt = (a.inner_text() or "").strip() or uid
                results.append({
                    "user_id": uid,
                    "username": txt[:60],
                    "is_following_us": is_follow_notif,
                    "we_are_following": False,
                    "previously_unfollowed": False,
                    "is_fresh": True,
                    "source": "notif" + ("_follow" if is_follow_notif else "_interaction"),
                })
                seen.add(uid)
                if len(results) >= limit:
                    return results
            h = page.evaluate("document.body.scrollHeight")
            if h == last_h:
                stuck += 1
                try:
                    page.keyboard.press("End")
                    time.sleep(1)
                except Exception:
                    pass
                if stuck >= 4:
                    break
            else:
                stuck = 0
            last_h = h
            page.evaluate("window.scrollBy(0, 1800)")
            time.sleep(1.5)
        except Exception as e:
            print(f"[notif_scroll_err] iter={i} {e}", flush=True)
            break
    return results


def dedupe_against_known(cands: list[dict]) -> list[dict]:
    """follow_log.success + followback_queue pending で重複除外

    2026-05-01 fix: status='failed' の queue エントリは skip しない
    (failed なら再試行すべき)。pending/success のみスキップ。
    """
    if not DB_PATH_V5.exists():
        return cands
    try:
        con = sqlite3.connect(f"file:{DB_PATH_V5}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT DISTINCT target_user_id FROM follow_log WHERE status='success'")
        already_followed = {r[0] for r in cur.fetchall() if r[0]}
        # Skip 'pending' (already queued) and 'completed' (already done)
        # Do NOT skip 'failed' — failed entries should be retried
        cur.execute(
            "SELECT DISTINCT follower_user_id FROM followback_queue "
            "WHERE status IN ('pending', 'completed')"
        )
        already_queued = {r[0] for r in cur.fetchall() if r[0]}
        con.close()
    except Exception as e:
        print(f"[dedupe_err] {e}", flush=True)
        return cands
    out = []
    skipped_followed = 0
    skipped_queued = 0
    for c in cands:
        uid = c.get("user_id")
        if uid in already_followed:
            c["we_are_following"] = True
            skipped_followed += 1
            continue
        if uid in already_queued:
            skipped_queued += 1
            continue
        out.append(c)
    print(f"[dedupe] in={len(cands)} out={len(out)} "
          f"skip_followed={skipped_followed} skip_queued={skipped_queued}",
          flush=True)
    return out


def main():
    parser = argparse.ArgumentParser(description="FOLLOWBACK multi-source (feed + notifications)")
    parser.add_argument("--limit", type=int, default=200,
                        help="per-source limit (total may be up to 2×limit)")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-feed", action="store_true",
                        help="skip S1 self-followers scrape")
    parser.add_argument("--skip-hop", action="store_true",
                        help="skip S2 2-hop expansion")
    parser.add_argument("--skip-notif", action="store_true",
                        help="skip S3 notifications scrape (enabled by default since 2026-05-01)")
    parser.add_argument("--hop-max-seeds", type=int, default=5,
                        help="max seed followers to expand in 2-hop (default: 5)")
    parser.add_argument("--hop-per-seed", type=int, default=50,
                        help="max candidates to collect per seed (default: 50)")
    args = parser.parse_args()

    print(f"[start] limit={args.limit} headless={args.headless} "
          f"dry_run={args.dry_run} skip_feed={args.skip_feed} "
          f"skip_hop={args.skip_hop} hop_seeds={args.hop_max_seeds} "
          f"hop_per_seed={args.hop_per_seed}", flush=True)

    launch_args = dict(
        user_data_dir=str(CHROME_PROFILE),
        headless=args.headless,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled", "--no-first-run"],
        ignore_default_args=["--enable-automation"],
    )
    if CHROME_EXE:
        launch_args["executable_path"] = str(CHROME_EXE)

    all_cands: list[dict] = []
    source_stats = {}

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(**launch_args)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        seed_uids: list[str] = []

        # S1: /{my_id}/followers  (own profile followers - 多く取れる)
        if not args.skip_feed:
            my_url = f"https://room.rakuten.co.jp/{MY_ACCOUNT_ID}/followers"
            feed = collect_from_followers(page, args.limit, my_url,
                                            source_tag="self_feed",
                                            is_following_us=True)
            # Fallback to /my/followers if self URL empty
            if not feed:
                feed = collect_from_followers(
                    page, args.limit,
                    "https://room.rakuten.co.jp/my/followers",
                    source_tag="my_feed", is_following_us=True)
            source_stats["feed"] = len(feed)
            all_cands.extend(feed)
            # Collect seeds for 2-hop (up to hop_max_seeds)
            seed_uids = [c["user_id"] for c in feed[:args.hop_max_seeds]]

        # S2: 2-hop expansion (friends-of-friends)
        if not args.skip_hop and seed_uids:
            print(f"[hop_start] seeds={len(seed_uids)} per_seed={args.hop_per_seed}",
                  flush=True)
            for si, seed in enumerate(seed_uids):
                hop_url = f"https://room.rakuten.co.jp/{seed}/followers"
                hop_cands = collect_from_followers(
                    page, args.hop_per_seed, hop_url,
                    source_tag=f"hop_{seed[:12]}",
                    is_following_us=False)
                source_stats[f"hop_{si}_{seed[:10]}"] = len(hop_cands)
                all_cands.extend(hop_cands)
                time.sleep(2)  # gentle rate limit

        # S3: /my/notifications (enabled by default - shows recent followers clearly)
        if not args.skip_notif:
            notif = collect_from_notifications(page, args.limit)
            source_stats["notif"] = len(notif)
            all_cands.extend(notif)

        ctx.close()

    print(f"[source_stats] {source_stats} total_raw={len(all_cands)}", flush=True)

    # Global dedupe by user_id within this run
    seen = set()
    merged = []
    for c in all_cands:
        uid = c.get("user_id")
        if uid in seen:
            continue
        seen.add(uid)
        merged.append(c)
    print(f"[merge] raw={len(all_cands)} unique={len(merged)}", flush=True)

    # DB-aware dedupe
    final = dedupe_against_known(merged)
    if not final:
        print("[result] stop_reason=all_sources_empty", flush=True)
        return 1

    if args.dry_run:
        for c in final[:20]:
            print(f"  [DRY] {c['user_id']:30s} source={c.get('source','?'):22s} "
                  f"uname={c['username'][:20]}", flush=True)
        print(f"[result] dry_run_complete count={len(final)}", flush=True)
        return 0

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import followback_executor as fbe
    try:
        out = fbe.enqueue_followers(final)
    except Exception as e:
        sys.stderr.write(f"[enqueue_err] {e}\n")
        return 1
    try:
        sys.stderr.write(f"[enqueue] {json.dumps(out, ensure_ascii=False)}\n")
        sys.stderr.flush()
    except Exception:
        pass
    print(f"[result] stop_reason=ok enqueued={out.get('inserted', '?')} "
          f"skipped={out.get('skipped', '?')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
