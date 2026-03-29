"""
SEO 自動化システム — CLIエントリーポイント

使い方:
  python run.py daemon        # 常駐ループ起動（メイン運用）
  python run.py generate      # 記事生成のみ（6件補充）
  python run.py post          # 即時投稿1件
  python run.py status        # キュー状況確認
  python run.py test-wp       # WordPress接続テスト
  python run.py init          # DB初期化のみ
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env", override=True)

import keyword_db as db
from seo_engine import run_daemon, run_once, check_and_generate, check_and_post, _log
from wp_poster import test_connection


def cmd_daemon():
    """常駐ループ起動"""
    run_daemon()


def cmd_generate():
    """記事生成のみ実行"""
    db.init_db()
    _log("=== 記事生成コマンド ===")
    generated = check_and_generate()
    s = db.get_status_summary()
    print(f"\n生成: {generated}件")
    print(f"キュー: queued={s['queued']}, posted={s['posted']}, failed={s['failed']}")
    print(f"キーワード: pending={s['kw_pending']}, used={s['kw_used']}")


def cmd_post():
    """即時投稿1件"""
    db.init_db()
    _log("=== 即時投稿コマンド ===")
    article = db.get_next_article_to_post()
    if not article:
        print("投稿待ち記事がありません。先に generate を実行してください。")
        return

    print(f"投稿対象: {article['title'][:60]}")
    answer = input("投稿しますか? [y/N]: ").strip().lower()
    if answer != "y":
        print("キャンセルしました。")
        return

    from seo_engine import _get_theme_from_keyword
    from wp_poster import post_to_wordpress

    article_id = article["id"]
    db.mark_article_posting(article_id)
    result = post_to_wordpress(
        title=article["title"],
        content=article["content"],
        theme=_get_theme_from_keyword(article["keyword"]),
        status="draft",
    )
    if result["success"]:
        db.mark_article_posted(article_id, result["wp_post_id"], result["wp_url"])
        print(f"✅ 投稿成功: post_id={result['wp_post_id']}")
        print(f"   URL: {result['wp_url']}")
    else:
        db.mark_article_failed(article_id, result["error"])
        print(f"❌ 投稿失敗: {result['error']}")


def cmd_status():
    """キュー状況確認"""
    db.init_db()
    s = db.get_status_summary()

    print("\n" + "=" * 50)
    print("SEO 自動化 — 現在の状況")
    print("=" * 50)
    print(f"  記事キュー (投稿待ち) : {s['queued']}件")
    print(f"  投稿中               : {s['posting']}件")
    print(f"  投稿済み             : {s['posted']}件")
    print(f"  失敗                 : {s['failed']}件")
    print(f"  キーワード残り       : {s['kw_pending']}件")
    print(f"  キーワード使用済み   : {s['kw_used']}件")

    from datetime import date
    today = date.today().isoformat()
    posted_hours = db.get_today_posted_hours()
    print(f"\n  本日({today})投稿時間帯: {posted_hours if posted_hours else 'なし'}")

    # 投稿待ち記事一覧（最大10件）
    import sqlite3
    conn = sqlite3.connect(str(db.DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, keyword, title, created_at FROM articles WHERE status='queued' ORDER BY created_at ASC LIMIT 10"
    ).fetchall()
    conn.close()

    if rows:
        print(f"\n  投稿待ち記事 (最大10件):")
        for r in rows:
            print(f"    [{r['id']}] {r['keyword']} | {r['title'][:50]}")

    print("=" * 50)


def cmd_test_wp():
    """WordPress接続テスト"""
    print("=== WordPress 接続テスト ===")
    ok = test_connection()
    if not ok:
        print("\n.envのWP_URL / WP_USER / WP_APP_PASSを確認してください。")


def cmd_init():
    """DB初期化"""
    db.init_db()
    s = db.get_status_summary()
    print("DB初期化完了")
    print(f"  キーワード: {s['kw_pending'] + s['kw_used']}件登録済み")


COMMANDS = {
    "daemon":   cmd_daemon,
    "generate": cmd_generate,
    "post":     cmd_post,
    "status":   cmd_status,
    "test-wp":  cmd_test_wp,
    "init":     cmd_init,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        print("利用可能なコマンド:")
        for cmd in COMMANDS:
            print(f"  python run.py {cmd}")
        sys.exit(1)

    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
