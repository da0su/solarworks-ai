"""
WordPress REST API 投稿モジュール

■ 仕様:
  - POST /wp-json/wp/v2/posts (Basic Auth: user + App Password)
  - 失敗時: 3回まで自動リトライ（1回目即時、2回目10秒後、3回目30秒後）
  - status: draft（承認前）→ publish（公開）はHRさんが手動確認後
  - カテゴリ・タグは themeから自動設定

■ 戻り値:
  {"success": bool, "wp_post_id": int, "wp_url": str, "error": str}
"""

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

WP_URL  = os.getenv("WP_URL", "").rstrip("/")
WP_USER = os.getenv("WP_USER", "")
WP_PASS = os.getenv("WP_APP_PASS", "")

MAX_RETRY    = 3
RETRY_DELAYS = [0, 10, 30]  # 秒

# テーマ→WordPress カテゴリスラッグ対応表（wp側にカテゴリが存在する前提）
THEME_CATEGORY_MAP = {
    "V2H":   "v2h",
    "蓄電池": "chikudenchi",
    "太陽光": "taiyokou",
}


def _auth_header() -> str:
    token = base64.b64encode(f"{WP_USER}:{WP_PASS}".encode()).decode()
    return f"Basic {token}"


def _get_or_create_category(category_slug: str) -> int | None:
    """カテゴリスラッグからIDを取得（なければNone）"""
    if not WP_URL:
        return None
    url = f"{WP_URL}/wp-json/wp/v2/categories?slug={category_slug}&per_page=1"
    try:
        req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if data:
                return data[0]["id"]
    except Exception:
        pass
    return None


def post_to_wordpress(
    title: str,
    content: str,
    theme: str,
    meta_desc: str = "",
    status: str = "draft",
) -> dict:
    """
    WordPress REST APIで記事を投稿（最大3リトライ）。

    Args:
        title:    記事タイトル
        content:  HTMLコンテンツ
        theme:    テーマ（V2H/蓄電池/太陽光）
        meta_desc: メタディスクリプション（Yoast等に設定）
        status:   "draft" or "publish"

    Returns:
        {"success": bool, "wp_post_id": int, "wp_url": str, "error": str}
    """
    if not WP_URL or not WP_USER or not WP_PASS:
        return {
            "success": False, "wp_post_id": 0, "wp_url": "",
            "error": "WordPress認証情報未設定（.envを確認）",
        }

    # カテゴリID取得
    cat_slug = THEME_CATEGORY_MAP.get(theme, "")
    cat_id = _get_or_create_category(cat_slug) if cat_slug else None

    payload = {
        "title":   title,
        "content": content,
        "status":  status,
    }
    if cat_id:
        payload["categories"] = [cat_id]

    # Yoast SEO メタディスクリプション（Yoast REST APIが有効な場合）
    if meta_desc:
        payload["meta"] = {
            "_yoast_wpseo_metadesc": meta_desc,
            "yoast_wpseo_metadesc":  meta_desc,
        }

    endpoint = f"{WP_URL}/wp-json/wp/v2/posts"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Authorization": _auth_header(),
        "Content-Type":  "application/json; charset=utf-8",
    }

    last_error = ""
    for attempt in range(MAX_RETRY):
        delay = RETRY_DELAYS[attempt] if attempt < len(RETRY_DELAYS) else 30
        if delay > 0:
            print(f"  [WP] リトライ {attempt+1}/{MAX_RETRY}... {delay}秒後")
            time.sleep(delay)

        try:
            req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                wp_post_id = result.get("id", 0)
                wp_url = result.get("link", "") or result.get("guid", {}).get("rendered", "")
                print(f"  [WP] 投稿成功 post_id={wp_post_id} url={wp_url}")
                return {
                    "success": True,
                    "wp_post_id": wp_post_id,
                    "wp_url": wp_url,
                    "error": "",
                }

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            last_error = f"HTTP {e.code}: {body}"
            print(f"  [WP] HTTPError attempt={attempt+1}: {last_error}")

        except urllib.error.URLError as e:
            last_error = f"URLError: {e.reason}"
            print(f"  [WP] URLError attempt={attempt+1}: {last_error}")

        except Exception as e:
            last_error = f"Exception: {e}"
            print(f"  [WP] Error attempt={attempt+1}: {last_error}")

    return {
        "success": False, "wp_post_id": 0, "wp_url": "",
        "error": f"3回リトライ後も失敗: {last_error}",
    }


def test_connection() -> bool:
    """WordPress接続テスト（GET /wp-json/wp/v2/posts?per_page=1）"""
    if not WP_URL:
        print("[WP] WP_URL未設定")
        return False
    try:
        url = f"{WP_URL}/wp-json/wp/v2/posts?per_page=1&status=any"
        req = urllib.request.Request(url, headers={"Authorization": _auth_header()})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            print(f"[WP] 接続OK: {WP_URL} (投稿数確認: {len(data)}件取得)")
            return True
    except Exception as e:
        print(f"[WP] 接続失敗: {e}")
        return False


if __name__ == "__main__":
    print("=== WordPress 接続テスト ===")
    ok = test_connection()
    if ok:
        print("接続成功。テスト投稿を行います（draft）...")
        r = post_to_wordpress(
            title="【テスト】接続確認記事 — 削除してください",
            content="<p>これはSEO自動化システムの接続テスト記事です。削除してください。</p>",
            theme="V2H",
            meta_desc="テスト記事",
            status="draft",
        )
        print(f"結果: {r}")
    else:
        print("接続失敗。.envのWP_URL/WP_USER/WP_APP_PASSを確認してください。")
