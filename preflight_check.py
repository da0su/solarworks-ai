"""Solar Works 事前チェックスクリプト

Desktop A / Desktop B で room_bot を起動する前に、
必要な環境がすべて揃っているかを自動チェックする。

使い方:
  python preflight_check.py
"""

import io
import os
import sys
from pathlib import Path

# Windows cp932 対策
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
ROOM_BOT_DIR = BASE_DIR / "rakuten-room" / "bot"

CHECKS = []
PASS = 0
FAIL = 0


def check(name, condition, detail_ok="OK", detail_ng="NG"):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}: {detail_ok}")
    else:
        FAIL += 1
        print(f"  ❌ {name}: {detail_ng}")


def main():
    global PASS, FAIL

    print("=" * 60)
    print("Solar Works 事前チェック")
    print("=" * 60)

    # --- 1. Python ---
    print("\n[1] Python")
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    check("Python バージョン",
          sys.version_info >= (3, 10),
          f"{py_ver}",
          f"{py_ver} (3.10以上が必要)")

    # --- 2. パッケージ ---
    print("\n[2] Python パッケージ")

    try:
        import schedule
        ver = getattr(schedule, "__version__", "installed")
        check("schedule", True, f"v{ver}")
    except ImportError:
        check("schedule", False, detail_ng="未インストール → pip install schedule")

    try:
        import playwright
        ver = getattr(playwright, "__version__", "installed")
        check("playwright", True, f"v{ver}")
    except ImportError:
        check("playwright", False, detail_ng="未インストール → pip install playwright")

    try:
        from slack_bolt import App
        check("slack-bolt", True, "インストール済み")
    except ImportError:
        check("slack-bolt", False, detail_ng="未インストール（Slack BOT不使用なら無視可）")

    # --- 3. Playwright ブラウザ ---
    print("\n[3] Playwright ブラウザ")
    pw_browsers = Path.home() / "AppData" / "Local" / "ms-playwright"
    chromium_dirs = list(pw_browsers.glob("chromium-*")) if pw_browsers.exists() else []
    check("Chromium ブラウザ",
          len(chromium_dirs) > 0,
          f"{chromium_dirs[0].name}" if chromium_dirs else "",
          "未インストール → python -m playwright install chromium")

    # --- 4. Chrome ---
    print("\n[4] Chrome 実行ファイル")
    sys.path.insert(0, str(ROOM_BOT_DIR))
    try:
        import config as bot_config
        chrome_path = Path(bot_config.CHROME_EXECUTABLE_PATH)
        check("Chrome パス",
              chrome_path.exists(),
              str(chrome_path),
              f"見つかりません: {chrome_path}")
    except Exception as e:
        check("config.py 読み込み", False, detail_ng=str(e))

    # --- 5. .env ---
    print("\n[5] 環境変数 (.env)")
    env_path = ROOM_BOT_DIR / ".env"
    check(".env ファイル",
          env_path.exists(),
          str(env_path),
          f"未作成 → {env_path}")

    if env_path.exists():
        env_content = env_path.read_text(encoding="utf-8")
        check("RAKUTEN_APP_ID",
              "RAKUTEN_APP_ID" in env_content and "=" in env_content,
              "設定あり",
              "未設定")
    else:
        check("RAKUTEN_APP_ID", False, detail_ng=".env ファイルがないため確認不可")

    # --- 6. データ ---
    print("\n[6] 投稿データ")
    source_items = ROOM_BOT_DIR / "data" / "source_items.json"
    check("source_items.json",
          source_items.exists(),
          str(source_items),
          f"未作成 → fetch_products.py で生成してください")

    if source_items.exists():
        import json
        try:
            items = json.loads(source_items.read_text(encoding="utf-8"))
            if isinstance(items, dict):
                items = items.get("items", items.get("posts", []))
            count = len(items)
            check("商品候補件数",
                  count >= 100,
                  f"{count}件",
                  f"{count}件 (100件以上推奨)")
        except Exception as e:
            check("source_items.json 読み込み", False, detail_ng=str(e))

    # --- 7. Chrome profile ---
    print("\n[7] Chrome プロファイル")
    profile_dir = ROOM_BOT_DIR / "data" / "chrome_profile"
    check("chrome_profile ディレクトリ",
          profile_dir.exists() and any(profile_dir.iterdir()) if profile_dir.exists() else False,
          str(profile_dir),
          "空または未作成 → python run.py login で作成されます")

    # --- 8. ログディレクトリ ---
    print("\n[8] ログ")
    logs_dir = BASE_DIR / "logs"
    check("logs ディレクトリ",
          logs_dir.exists(),
          str(logs_dir),
          "未作成（scheduler 起動時に自動作成されます）")

    # --- 結果 ---
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"結果: {PASS}/{total} 項目 PASS")
    if FAIL > 0:
        print(f"       {FAIL} 項目が未対応です")
        print("\n上記の ❌ を解消してから運用を開始してください。")
    else:
        print("\nすべてのチェックに合格しました！")
        print("python run.py login → scheduler --test → scheduler.py で運用開始できます。")
    print("=" * 60)

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
