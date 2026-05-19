"""Profile 健全性 watchdog (CEO 5/17 真因確定 + Codex 18回目 review 反映).

【目的】 chrome_profile_post が想定アカウントに login しているか毎回 check.
不一致なら即 Fail-Fast (起動 noop)・CEO 通知.

【背景】 2026-05-17 21:57 真因発覚:
- chrome_profile_post が別 (空) アカウントに切替わっていた
- 5/12-5/17 6日間 全件 false success の真因
- DB status='posted' だけ見て my ROOM 商品数を見ていなかった

【check ルール】
1. my ROOM page (room.rakuten.co.jp/my/items) を取得
2. 商品数 + フォロワー数 + フォロー数 が 期待 baseline 以上か確認
3. baseline 未満なら GateError (別アカウント疑い)
4. baseline はstate/profile_baseline.json に永続化 (CEO 確認後更新)

【usage】
    from shared.profile_health import check_profile_health, ProfileError
    try:
        check_profile_health(bm)  # bm = BrowserManager
    except ProfileError as e:
        # 別アカウント / cookie 破損
        notify_slack(f"PROFILE 異常: {e}")
        sys.exit(31)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "state" / "profile_baseline.json"

if TYPE_CHECKING:
    from playwright.sync_api import Page


class ProfileError(Exception):
    """Profile 健全性異常 (別アカウント / cookie 破損 / 未 login)"""


def fetch_room_cumulative_via_fallback_chain(profile_chain: list[str] = None,
                                              timeout_ms: int = 20000,
                                              min_item_count: int = 50) -> dict:
    """profile fallback chain で ROOM 累計を取得.

    Codex 29回目 #8 + 30回目 #8 反映 (CEO 5/20 累計突合):
    - chrome_profile_post が空アカウントに切替わっている疑い → 他 profile を順に試行
    - baseline 保存済 + URL に room_id 含む確認で「期待アカウント一致」も check
      (item_count >= 50 単独だと別本番アカウントを誤承認する潜在リスク回避)

    Args:
        profile_chain: 試行する profile 名のリスト (None で default chain)
        timeout_ms: 各 profile への browser timeout
        min_item_count: 商品数最低閾値 (これ未満は invalid 判定)

    Returns:
        {"profile_used": str, "fingerprint": dict, "tried": [str], "errors": {profile: err}}
        失敗時: {"_error": "all profiles failed", "tried": [str], "errors": dict}
    """
    if profile_chain is None:
        # default chain: post → follow → like → followback の順
        # post が壊れていた場合の救済 (各 profile は別 cookie の可能性)
        profile_chain = ["post", "follow", "like", "followback"]

    import sys
    from pathlib import Path
    REPO = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(REPO / "rakuten-room" / "bot"))
    try:
        from executor.browser_manager import BrowserManager
    except Exception as e:
        return {"_error": f"BrowserManager import: {e}", "tried": [], "errors": {}}

    # 期待 ROOM_ID (config から取得 - 別アカウント切替検出 ID 一致 check 用)
    expected_room_id = ""
    try:
        sys.path.insert(0, str(REPO / "rakuten-room" / "bot"))
        import config as _config
        expected_room_id = getattr(_config, "ROOM_ID", "")
    except Exception:
        pass

    # Codex 32回目 #7 fix: load_baseline は同 module 内 forward reference (line 220+).
    # Python は call time 解決なので OK だが、念のため try-guard で fallback empty
    try:
        baseline = load_baseline() or {}
    except Exception as _e:
        baseline = {}

    tried = []
    errors: dict = {}

    # ROOM_ID が含まれるべき URL pattern (Codex 31回目 #3 反映: whitelist 列挙)
    # /my/items は ROOM_ID 含まない可能性ありなのでこの場合は URL check skip
    MY_PAGE_URL_PATTERNS = ("/my/items", "/my/")

    for profile_name in profile_chain:
        tried.append(profile_name)
        bm = None
        try:
            bm = BrowserManager(action=profile_name)
            bm.start()
            st = bm.check_login_status()
            if not st.get("logged_in"):
                errors[profile_name] = "not logged in"
                continue
            # Codex 34回目 #1 fix: fetch_my_room_fingerprint は 冒頭で
            # page.goto("https://room.rakuten.co.jp/my/items") する設計だが、
            # 万一 navigate 失敗 / 関数仕様変更時の二重防御で 明示 goto を追加.
            # （URL 残留=failure 判定は維持・goto 失敗時は例外で fail-closed）
            try:
                bm.page.goto(
                    "https://room.rakuten.co.jp/my/items",
                    timeout=timeout_ms,
                    wait_until="domcontentloaded",
                )
            except Exception as _e:
                errors[profile_name] = f"my/items navigate failed: {str(_e)[:200]}"
                continue
            fp = fetch_my_room_fingerprint(bm.page, timeout_ms=timeout_ms)
            # Codex 31回目 #2 fix: item_count int 変換安全化 (型予防)
            raw_ic = fp.get("item_count")
            if raw_ic is None:
                errors[profile_name] = f"item_count None (page parse failed): fp={fp}"
                continue
            try:
                ic_int = int(raw_ic)
            except (TypeError, ValueError):
                errors[profile_name] = f"item_count parse failed: {raw_ic!r} (fp={fp})"
                continue
            if ic_int < min_item_count:
                # 空アカウント疑い (期待される本番アカウントは商品数 >> 50)
                errors[profile_name] = (
                    f"商品数 {ic_int} < {min_item_count} (空アカウント疑い)"
                )
                continue
            # Codex 30回目 #8 + 31回目 #3 + 32回目 #1 + 35回目 #1 fix:
            # URL or DOM 抽出 ROOM_ID で確認. expected_room_id が未設定でも fail-closed.
            # /my/items は URL に ROOM_ID 含まないが、canonical/og:url から抽出可能.
            # 多層防御: URL match OR extracted_room_id match (どちらか fail で reject)
            url = fp.get("url", "") or ""
            extracted_rid = fp.get("extracted_room_id", "") or ""
            is_my_page = any(p in url for p in MY_PAGE_URL_PATTERNS)
            # Codex 35回目 #1 fix: expected_room_id が未設定でも extracted_rid が
            # 取れた場合は baseline.extracted_room_id と比較. 両方無いなら fail-closed.
            baseline_rid = baseline.get("extracted_room_id", "") or ""
            effective_expected = expected_room_id or baseline_rid
            if not effective_expected:
                # config に ROOM_ID 未設定 + baseline にも無い → 確認不能 → fail-closed
                # (CEO 5/20「虚偽報告ゼロ」優先で別本番アカウント誤承認リスクを排除)
                errors[profile_name] = (
                    f"本人性確認不能: expected_room_id 未設定 かつ baseline ROOM_ID も無し "
                    f"(URL={url!r}, extracted={extracted_rid!r}) → fail-closed"
                )
                continue
            url_match = effective_expected in url
            ext_match = effective_expected == extracted_rid
            if not url_match and not ext_match:
                if is_my_page and not extracted_rid:
                    # /my/items + DOM 抽出失敗 → 確認できず → fail-closed (CEO 5/20)
                    errors[profile_name] = (
                        f"ROOM_ID 確認不能: URL={url!r}, extracted={extracted_rid!r}, "
                        f"expected={effective_expected!r} (DOM 抽出失敗 → fail-closed)"
                    )
                    continue
                errors[profile_name] = (
                    f"ROOM_ID 不一致: URL={url!r}, extracted={extracted_rid!r}, "
                    f"expected={effective_expected!r} (別アカウント疑い)"
                )
                continue
            # baseline 比較 (大幅減少 = 50件超なら別アカウント疑い)
            # Codex 31回目 #3: baseline check は AND ではなく独立して実施.
            baseline_ic_raw = baseline.get("item_count")
            if baseline_ic_raw is not None:
                try:
                    baseline_ic = int(baseline_ic_raw)
                    delta = ic_int - baseline_ic
                    if delta < -50:
                        errors[profile_name] = (
                            f"商品数が baseline {baseline_ic} から大幅減少 "
                            f"→ {ic_int} (delta={delta})"
                        )
                        continue
                except (TypeError, ValueError):
                    pass  # baseline 不正 → skip
            # OK
            return {
                "profile_used": profile_name,
                "fingerprint": fp,
                "tried": tried,
                "errors": errors,
                "expected_room_id": expected_room_id,
            }
        except Exception as e:
            errors[profile_name] = f"exception: {str(e)[:200]}"
        finally:
            if bm:
                try:
                    bm.stop()
                except Exception:
                    pass

    return {
        "_error": "all profiles failed",
        "tried": tried,
        "errors": errors,
        "expected_room_id": expected_room_id,
    }


def _extract_room_id_from_page(page: "Page") -> str:
    """ページから ROOM_ID を抽出 (Codex 32回目 #1 fix).

    /my/items は URL に ROOM_ID を含まないが、DOM 内に canonical URL や
    プロフィールリンクとして含まれる. それを抽出して expected と比較する.

    Returns: ROOM_ID 文字列 or "" (抽出失敗)
    """
    try:
        return page.evaluate(r"""() => {
            // 1. canonical link
            const canon = document.querySelector('link[rel="canonical"]');
            if (canon && canon.href) {
                const m = canon.href.match(/room\.rakuten\.co\.jp\/(room_[a-zA-Z0-9_]+|salt_[a-zA-Z0-9_]+)/);
                if (m) return m[1];
            }
            // 2. og:url meta
            const ogUrl = document.querySelector('meta[property="og:url"]');
            if (ogUrl && ogUrl.content) {
                const m = ogUrl.content.match(/room\.rakuten\.co\.jp\/(room_[a-zA-Z0-9_]+|salt_[a-zA-Z0-9_]+)/);
                if (m) return m[1];
            }
            // 3. 任意のリンクから先頭 ROOM_ID 風 anchor
            const a = document.querySelector('a[href*="/room_"], a[href*="/salt_"]');
            if (a && a.getAttribute) {
                const href = a.getAttribute('href') || '';
                const m = href.match(/^\/(room_[a-zA-Z0-9_]+|salt_[a-zA-Z0-9_]+)/);
                if (m) return m[1];
            }
            return '';
        }""") or ""
    except Exception:
        return ""


def fetch_my_room_fingerprint(page: "Page", timeout_ms: int = 20000) -> dict:
    """my ROOM page から指紋を取得.

    Returns: {
        'item_count': int,       # 商品数
        'follower_count': int,   # フォロワー数
        'follow_count': int,     # フォロー数
        'url': str,
        'fetched_at': str,
    }
    """
    page.goto("https://room.rakuten.co.jp/my/items", timeout=timeout_ms, wait_until="domcontentloaded")
    # angular app render 待ち
    import time
    time.sleep(3)

    fingerprint = page.evaluate(r"""() => {
        const out = {};
        // Codex 19回目 #2 反映: 桁区切り (3,500 / ３，５００) + 全角数字対応
        const normalize = (s) => {
            if (!s) return '';
            return s.normalize('NFKC').replace(/[,，]/g, '');
        };
        const text = normalize(document.body.innerText || '');
        const grab = (label) => {
            const re = new RegExp(label + '\\s*\\n?\\s*(\\d+)');
            const m = text.match(re);
            return m ? parseInt(m[1]) : null;
        };
        // CEO 5/18 指示: ROOM 内のすべての累計を取得 (スプシ突合用)
        out.item_count = grab('商品');
        out.follow_count = grab('フォロー(?!ー)');
        out.follower_count = grab('フォロワー');
        out.coordinate_count = grab('コーディネート');
        out.collection_count = grab('コレクション');
        out.like_count = grab('いいね');
        return out;
    }""")
    # Codex 32回目 #1 fix: ROOM_ID を DOM canonical/og:url から抽出
    extracted_room_id = _extract_room_id_from_page(page)
    return {
        "item_count": fingerprint.get("item_count"),
        "follower_count": fingerprint.get("follower_count"),
        "follow_count": fingerprint.get("follow_count"),
        "coordinate_count": fingerprint.get("coordinate_count"),
        "collection_count": fingerprint.get("collection_count"),
        "like_count": fingerprint.get("like_count"),
        "url": page.url,
        "extracted_room_id": extracted_room_id,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_baseline(fingerprint: dict, note: str = "") -> None:
    """baseline 保存 (Codex 35回目 #1 反映: extracted_room_id を必ず含める)."""
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = dict(fingerprint)
    data["note"] = note
    data["saved_at"] = datetime.now().isoformat(timespec="seconds")
    # extracted_room_id が未設定でも空文字で保存 (downstream の get(...,"") 対応)
    data.setdefault("extracted_room_id", "")
    BASELINE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def check_profile_health(page: "Page",
                          min_item_count: int = 100,
                          min_follower_count: int = 1000) -> dict:
    """Profile 健全性 check. 不一致なら ProfileError raise.

    Args:
        page: Playwright Page (login 済前提)
        min_item_count: 期待最低 商品数 (これ未満なら別アカウント疑い)
        min_follower_count: 期待最低 フォロワー数

    Returns: fingerprint dict

    Raises: ProfileError - 別アカウント / cookie 破損
    """
    fp = fetch_my_room_fingerprint(page)
    baseline = load_baseline()

    # 基本 check (絶対閾値)
    if fp.get("item_count") is None:
        raise ProfileError(f"my ROOM page から商品数取得失敗: fp={fp}")
    if fp["item_count"] < min_item_count:
        raise ProfileError(
            f"商品数 {fp['item_count']} < min_item_count {min_item_count} "
            f"(別アカウント疑い / login 期待アカウントと不一致)"
        )
    if fp.get("follower_count") is not None and fp["follower_count"] < min_follower_count:
        raise ProfileError(
            f"フォロワー数 {fp['follower_count']} < min_follower_count {min_follower_count} "
            f"(別アカウント疑い)"
        )

    # baseline 比較 (大幅減少なら異常)
    if baseline and baseline.get("item_count"):
        delta = fp["item_count"] - baseline["item_count"]
        if delta < -50:  # 50件以上減ったら異常
            raise ProfileError(
                f"商品数が baseline {baseline['item_count']} から大幅減少 → {fp['item_count']} (delta={delta})"
            )
    return fp


# Exit codes
EXIT_OK = 0
EXIT_PROFILE_ERROR = 31


if __name__ == "__main__":
    """CLI: profile 状態を確認する.
    python shared/profile_health.py [--save-baseline "備考"]
    """
    sys.stdout.reconfigure(encoding="utf-8")
    sys.path.insert(0, str(REPO_ROOT / "rakuten-room" / "bot"))
    from executor.browser_manager import BrowserManager

    save_mode = "--save-baseline" in sys.argv
    note = ""
    if save_mode:
        idx = sys.argv.index("--save-baseline")
        if idx + 1 < len(sys.argv):
            note = sys.argv[idx + 1]

    bm = BrowserManager(action="post")
    bm.start()
    try:
        status = bm.check_login_status()
        if not status.get("logged_in"):
            print("ERR: not logged in", file=sys.stderr)
            sys.exit(2)
        fp = fetch_my_room_fingerprint(bm.page)
        print(f"\n=== Profile fingerprint ===")
        for k, v in fp.items():
            print(f"  {k}: {v}")
        baseline = load_baseline()
        if baseline:
            print(f"\n=== Baseline (saved) ===")
            for k, v in baseline.items():
                print(f"  {k}: {v}")
            for k in ("item_count", "follower_count", "follow_count"):
                if k in fp and k in baseline:
                    delta = (fp[k] or 0) - (baseline[k] or 0)
                    sign = "+" if delta >= 0 else ""
                    print(f"  Δ {k}: {sign}{delta}")
        else:
            print("\n=== Baseline 未設定 (初回 --save-baseline で保存) ===")

        if save_mode:
            save_baseline(fp, note=note)
            print(f"\n=> baseline 保存: {BASELINE_PATH}")
    finally:
        bm.stop()
