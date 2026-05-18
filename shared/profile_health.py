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
    return {
        "item_count": fingerprint.get("item_count"),
        "follower_count": fingerprint.get("follower_count"),
        "follow_count": fingerprint.get("follow_count"),
        "coordinate_count": fingerprint.get("coordinate_count"),
        "collection_count": fingerprint.get("collection_count"),
        "like_count": fingerprint.get("like_count"),
        "url": page.url,
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
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = dict(fingerprint)
    data["note"] = note
    data["saved_at"] = datetime.now().isoformat(timespec="seconds")
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
