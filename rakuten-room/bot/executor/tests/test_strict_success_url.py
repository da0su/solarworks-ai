"""POST 成功判定 STRICT_SUCCESS_URL regex 回帰テスト.

Codex 5/16 6回目 review 指摘:
- /items/<id>/like や /items/<id>/edit を success 通過させないこと
- /items/<id>/<sub-path> 系の非カノニカル下位パスは全て false
"""
from __future__ import annotations

import sys
from pathlib import Path

# allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from executor.post_executor import STRICT_SUCCESS_URL


def _check(url: str, expected: bool, label: str) -> None:
    actual = bool(STRICT_SUCCESS_URL.match(url))
    assert actual == expected, f"[{label}] {url!r} expected={expected} got={actual}"


def test_positive_cases():
    _check("https://room.rakuten.co.jp/room_abc/items/1234567", True, "PC basic")
    _check("https://sp.room.rakuten.co.jp/room_abc/items/1234567", True, "SP basic")
    _check("https://room.rakuten.co.jp/owner_xyz/items/9", True, "single digit")
    _check(
        "https://room.rakuten.co.jp/owner_xyz/items/123?utm_source=share",
        True,
        "with query",
    )
    _check(
        "https://room.rakuten.co.jp/owner_xyz/items/123#section",
        True,
        "with fragment",
    )
    # Codex 7回目 review 指摘: 末尾スラッシュ (SP 版が返す可能性) も許容
    _check("https://room.rakuten.co.jp/owner_xyz/items/123/", True, "trailing slash")
    _check("https://sp.room.rakuten.co.jp/owner_xyz/items/123/?utm=share", True, "trailing slash + query")


def test_negative_codex_pointed():
    # Codex 6回目 review で指摘された偽陽性候補
    _check("https://room.rakuten.co.jp/owner/items/123/like", False, "like subpath")
    _check("https://room.rakuten.co.jp/owner/items/123/edit", False, "edit subpath")
    _check("https://room.rakuten.co.jp/owner/items/123/comments", False, "comments subpath")
    _check("https://room.rakuten.co.jp/owner/items/123/anything", False, "any subpath")


def test_negative_url_residue():
    _check("https://room.rakuten.co.jp/mix?itemcode=xxx", False, "mix residue")
    _check("https://room.rakuten.co.jp/mix/collect", False, "mix collect")
    _check("https://room.rakuten.co.jp/common/error", False, "error page")
    _check("https://room.rakuten.co.jp/", False, "root")


def test_negative_bad_host_or_form():
    _check("http://room.rakuten.co.jp/owner/items/123", False, "http (not https)")
    _check("https://other.com/owner/items/123", False, "other host")
    _check("https://room.rakuten.co.jp/owner/items/abc", False, "non-digit id")
    _check("https://room.rakuten.co.jp/owner/items/", False, "missing id")
    _check("https://room.rakuten.co.jp/owner//items/123", False, "double slash")
    # Codex 7回目 review 指摘: about:blank / chrome-error 等 非 http スキーム
    _check("about:blank", False, "about:blank")
    _check("chrome-error://chromewebdata/", False, "chrome-error")
    _check("", False, "empty url")


if __name__ == "__main__":
    for fn in [test_positive_cases, test_negative_codex_pointed, test_negative_url_residue, test_negative_bad_host_or_form]:
        fn()
        print(f"  PASS {fn.__name__}")
    print("ALL OK")
