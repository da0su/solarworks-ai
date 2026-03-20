"""ROOM BOT v5.0 - エントリーポイント

使い方:
  # 初回ログイン（セッション保存）
  python run.py login

  # 1件投稿
  python run.py post --url "楽天商品URL" --text "投稿文"

  # バッチ投稿（JSONから複数件）
  python run.py batch --file posts.json
  python run.py batch --file posts.json --count 10

  # 投稿プレビュー（dry-run: 投稿せずに内容を確認）
  python run.py preview --file posts.json

  # 投稿計画生成 → キュー実行（scheduler連携）
  python run.py plan                              # 計画生成
  python run.py execute --limit 35 --min-wait 30 --max-wait 50  # バッチ実行
  python run.py daily                             # plan+execute一括

  # いいねBOT
  python run.py like                              # デフォルト件数でいいね
  python run.py like --limit 10                   # 10件だけいいね

  # フォローBOT
  python run.py follow                            # デフォルト件数でフォロー
  python run.py follow --limit 20                 # 20件だけフォロー

  # CEOコマンド（ROOM運用管理）
  python run.py room on                           # ROOM運用ON
  python run.py room off                          # ROOM運用OFF
  python run.py room status                       # 状態確認
  python run.py room plus like 10                 # 臨時いいね10件
  python run.py room plus follow 20               # 臨時フォロー20件
  python run.py room plus post 30                 # 臨時投稿30件
  python run.py room generate-month               # 月間スケジュール生成

  # スケジュール管理
  python run.py schedule --show                   # 今日のスケジュール表示
  python run.py schedule --generate               # 今日のスケジュール生成
  python run.py schedule --generate --date 2026-03-18  # 指定日のスケジュール生成
"""

import argparse
import io
import json
import sys
from datetime import datetime
from pathlib import Path

# Windows cp932 で絵文字を出力できるよう UTF-8 に切り替え
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

import config
from executor.browser_manager import BrowserManager
from executor.post_executor import PostExecutor
from executor.batch_runner import BatchRunner, load_posts_json
from logger.logger import setup_logger, save_post_result

logger = setup_logger()


def cmd_login():
    """CEOの手動ログイン → セッション保存"""
    logger.info("=== ログインモード ===")
    bm = BrowserManager()
    try:
        bm.start()
        bm.login_manual()

        # ログイン確認（複合判定）
        status = bm.check_login_status()
        print("\n" + "=" * 60)
        if status["logged_in"]:
            print(f"ログイン成功! (判定: {status['method']})")
            print(f"  URL:   {status['url']}")
            print(f"  Title: {status['title']}")
            print("")
            print("persistent contextにcookieが保存されました。")
            print("次回から `python run.py batch` で自動投稿できます。")
        else:
            print(f"ログイン確認失敗 (判定: {status['method']})")
            print(f"  URL:   {status['url']}")
            print(f"  Title: {status['title']}")
            if status["screenshot"]:
                print(f"  SS:    {status['screenshot']}")
            print("")
            print("persistent contextにcookieが保存されている可能性があります。")
            print("`python run.py batch` を試してみてください。")
        print("=" * 60)
    finally:
        bm.stop()
        print("\npersistent contextのcookieはブラウザ終了時に自動保存されています。")


def cmd_post(product_url: str, review_text: str):
    """1件の投稿を実行"""
    logger.info("=" * 50)
    logger.info("=== ROOM BOT v2 - 投稿実行 ===")
    logger.info("=" * 50)
    logger.info(f"商品URL: {product_url}")
    logger.info(f"投稿文: {review_text[:50]}...")

    bm = BrowserManager()
    try:
        # ブラウザ起動
        bm.start()

        # ログイン確認（複合判定）
        status = bm.check_login_status()
        if not status["logged_in"]:
            logger.error(f"ログイン確認失敗: method={status['method']} url={status['url']}")
            print("\n" + "=" * 60)
            print(f"ログイン確認失敗 (判定: {status['method']})")
            print(f"  URL:   {status['url']}")
            print(f"  Title: {status['title']}")
            print("")
            print("  python run.py login でログインしてください。")
            print("=" * 60)
            return

        # 投稿実行
        executor = PostExecutor(bm)
        result = executor.execute(product_url, review_text)

        # 結果表示
        print("\n" + "=" * 60)
        if result["success"]:
            logger.info("投稿成功!")
            print("投稿成功!")
            if result["room_url"]:
                print(f"ROOM URL: {result['room_url']}")

            # POST_LOG.json に記録
            post_record = {
                "post_id": f"{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "product_url": product_url,
                "review_text_preview": review_text[:80] + "...",
                "status": "posted",
                "posted_at": datetime.now().isoformat(),
                "room_url": result["room_url"],
                "method": "room_bot_v2_mvp",
            }
            save_post_result(post_record)
            logger.info("投稿ログを POST_LOG.json に保存しました")
        else:
            logger.error(f"投稿失敗: {result['error']}")
            print(f"投稿失敗: {result['error']}")
            print("")
            print("スクリーンショットを確認してください:")
            for ss in result.get("screenshots", []):
                print(f"  {ss}")

        print("=" * 60)

        # ログファイルの場所
        today = datetime.now().strftime("%Y-%m-%d")
        print(f"\nログ: {config.LOG_DIR / f'{today}.log'}")
        print(f"スクリーンショット: {config.SCREENSHOT_DIR / today}/")

    finally:
        bm.stop()


def cmd_batch(file_path: str | None, use_stdin: bool, count: int | None,
              min_wait: float | None = None, max_wait: float | None = None):
    """JSONファイルまたは標準入力からバッチ投稿を実行"""
    logger.info("=" * 50)
    logger.info("=== ROOM BOT v2 - バッチ投稿 ===")
    logger.info("=" * 50)

    # データ読み込み
    if use_stdin:
        logger.info("標準入力からJSONを読み込みます...")
        try:
            raw = sys.stdin.read()
            data = json.loads(raw)
            if isinstance(data, list):
                posts = data
            elif isinstance(data, dict) and "posts" in data:
                posts = data["posts"]
            else:
                print("エラー: JSONの形式が不正です。配列 or {\"posts\": [...]} で指定してください。")
                sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"エラー: JSONパース失敗: {e}")
            sys.exit(1)
        logger.info(f"標準入力から {len(posts)}件 読み込み完了")
    else:
        try:
            posts = load_posts_json(file_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"エラー: {e}")
            sys.exit(1)
        logger.info(f"JSONファイル: {file_path} ({len(posts)}件)")

    if not posts:
        print("エラー: 投稿データが0件です")
        sys.exit(1)

    effective = len(posts[:count]) if count else len(posts)
    print(f"\n投稿データ: {len(posts)}件中 {effective}件を投稿します")
    logger.info(f"投稿件数: {effective}件")

    runner = BatchRunner(posts, count=count, min_wait=min_wait, max_wait=max_wait)
    if min_wait is not None:
        logger.info(f"待機間隔: {min_wait}〜{max_wait or min_wait}秒（カスタム）")
    summary = runner.run()

    # ログファイルの場所
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\nログ: {config.LOG_DIR / f'{today}.log'}")
    print(f"スクリーンショット: {config.SCREENSHOT_DIR / today}/")

    # JSON結果出力（n8n等の外部ツールが結果をパースできるように）
    print(f"\n__BATCH_RESULT_JSON__")
    print(json.dumps({
        "total": summary["total"],
        "success": summary["success"],
        "failed": summary["failed"],
        "skipped": summary.get("skipped", 0),
    }, ensure_ascii=False))

    # 失敗があった場合は終了コード1
    if summary.get("failed", 0) > 0 or summary.get("aborted"):
        sys.exit(1)


def cmd_check_collect(file_path: str | None, use_stdin: bool, count: int | None):
    """collect対応チェック（ブラウザで実際にcollect URLを開いて検証する）"""
    from urllib.parse import quote

    # データ読み込み
    if use_stdin:
        try:
            raw = sys.stdin.read()
            data = json.loads(raw)
            posts = data if isinstance(data, list) else data.get("posts", [])
        except json.JSONDecodeError as e:
            print(f"エラー: JSONパース失敗: {e}")
            sys.exit(1)
    else:
        try:
            posts = load_posts_json(file_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"エラー: {e}")
            sys.exit(1)

    if not posts:
        print("エラー: 投稿データが0件です")
        sys.exit(1)

    target_posts = posts[:count] if count else posts

    print("\n" + "=" * 60)
    print(f"collect対応チェック - {len(target_posts)}件")
    print("=" * 60)

    bm = BrowserManager()
    ok_list = []
    ng_list = []

    try:
        bm.start()

        # ログイン確認
        login_status = bm.check_login_status()
        if not login_status["logged_in"]:
            print(f"\n未ログインです。先に python run.py login を実行してください。")
            return

        executor = PostExecutor(bm)

        for i, post in enumerate(target_posts):
            num = i + 1
            title = post.get("title", "(タイトルなし)")
            url = post.get("url", "")

            if not url or not url.startswith("http"):
                print(f"  [{num}/{len(target_posts)}] SKIP  {title[:40]} (URL不正)")
                ng_list.append({"title": title, "url": url, "reason": "URL不正", "screenshot": None})
                continue

            result = executor.check_collect(url, save_screenshot=True)
            ss = result.get("screenshot", "")
            if result["supported"]:
                print(f"  [{num}/{len(target_posts)}] OK    {title[:40]}  ({result['reason']})")
                ok_list.append({"title": title, "url": url, "reason": result["reason"], "screenshot": ss})
            else:
                print(f"  [{num}/{len(target_posts)}] NG    {title[:40]}  ({result['reason']})")
                ng_list.append({"title": title, "url": url, "reason": result["reason"], "screenshot": ss})

    finally:
        bm.stop()

    print("\n" + "=" * 60)
    print(f"  OK: {len(ok_list)}件  /  NG: {len(ng_list)}件")

    if ok_list:
        print("\n--- collect対応OK ---")
        for item in ok_list:
            print(f"  o {item['title'][:40]}")
            print(f"    {item['url']}")
            if item["screenshot"]:
                print(f"    screenshot: {item['screenshot']}")

    if ng_list:
        print("\n--- collect非対応NG ---")
        for item in ng_list:
            print(f"  x {item['title'][:40]}")
            print(f"    {item['url']}")
            print(f"    reason: {item['reason']}")
            if item.get("screenshot"):
                print(f"    screenshot: {item['screenshot']}")

    print("=" * 60)
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\nスクリーンショット: {config.SCREENSHOT_DIR / today}/")
    print("上記のスクリーンショットを目視確認してから batch を実行してください。")


def cmd_investigate(file_path: str | None, use_stdin: bool, count: int | None):
    """ROOM投稿導線の調査モード

    商品ページ・ROOM関連ページを開いて、投稿に使える要素を調査する。
    結果は data/investigate_report.txt に保存される。
    """
    # データ読み込み
    if file_path or use_stdin:
        if use_stdin:
            try:
                raw = sys.stdin.read()
                data = json.loads(raw)
                posts = data if isinstance(data, list) else data.get("posts", [])
            except json.JSONDecodeError as e:
                print(f"エラー: JSONパース失敗: {e}")
                sys.exit(1)
        else:
            try:
                posts = load_posts_json(file_path)
            except (FileNotFoundError, ValueError) as e:
                print(f"エラー: {e}")
                sys.exit(1)
        target_posts = posts[:count] if count else posts[:3]
    else:
        target_posts = []

    print("\n" + "=" * 70)
    print("ROOM投稿導線 調査モード")
    print("=" * 70)

    bm = BrowserManager()
    all_reports = []

    try:
        bm.start()

        # ログイン確認
        login_status = bm.check_login_status()
        if not login_status["logged_in"]:
            print("未ログインです。先に python run.py login を実行してください。")
            return
        print(f"ログイン確認OK ({login_status['method']})")

        executor = PostExecutor(bm)

        # === Phase 1: ROOM内部の投稿導線を調査 ===
        print("\n--- Phase 1: ROOM内部ページの調査 ---")
        room_reports = executor.investigate_room_my_page()
        for label, report in room_reports.items():
            text = PostExecutor.format_investigation_report(report, label)
            print(text)
            all_reports.append(text)

        # === Phase 2: 商品ページ上のROOM連携要素を調査 ===
        if target_posts:
            print("\n--- Phase 2: 商品ページの調査 ---")
            for i, post in enumerate(target_posts):
                url = post.get("url", "")
                title = post.get("title", "(タイトルなし)")
                if not url:
                    continue
                print(f"\n[{i+1}/{len(target_posts)}] {title[:50]}")
                report = executor.investigate_product_page(url)
                text = PostExecutor.format_investigation_report(report, f"商品ページ: {title[:30]}")
                print(text)
                all_reports.append(text)
                executor._human_delay(2.0, 3.0)

        # === Phase 3: 楽天ブックスも試す ===
        print("\n--- Phase 3: 楽天ブックス商品ページの調査 ---")
        books_url = "https://books.rakuten.co.jp/rb/16834204/"  # 人は話し方が9割
        report = executor.investigate_product_page(books_url)
        text = PostExecutor.format_investigation_report(report, "楽天ブックス: 人は話し方が9割")
        print(text)
        all_reports.append(text)

    finally:
        bm.stop()

    # レポートをファイルに保存
    report_path = config.DATA_DIR / "investigate_report.txt"
    report_content = "\n".join(all_reports)
    report_path.write_text(report_content, encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"調査完了")
    print(f"レポート: {report_path}")
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"スクリーンショット: {config.SCREENSHOT_DIR / today}/")
    print("=" * 70)
    print("\n上記のレポートとスクリーンショットを確認し、")
    print("ROOM投稿導線の仮説を立ててください。")


def cmd_plan(queue_date: str | None, max_items: int | None, source: str | None):
    """投稿計画を生成してSQLiteキューに登録する"""
    from planner.daily_planner import generate_daily_plan, format_plan_report

    plan = generate_daily_plan(
        queue_date=queue_date,
        max_items=max_items,
        source_path=source,
    )
    print(format_plan_report(plan))

    if plan["planned"] > 0:
        print(f"\n次のステップ:")
        print(f"  python run.py execute              # キューを実行")
        print(f"  python run.py queue-status          # 状況確認")


def cmd_execute(queue_date: str | None, limit: int | None,
                min_wait: float | None, max_wait: float | None):
    """SQLiteキューから投稿を実行する"""
    from planner.queue_executor import QueueExecutor

    executor = QueueExecutor(
        queue_date=queue_date,
        limit=limit,
        min_wait=min_wait,
        max_wait=max_wait,
    )
    summary = executor.run()

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\nログ: {config.LOG_DIR / f'{today}.log'}")
    print(f"スクリーンショット: {config.SCREENSHOT_DIR / today}/")

    if summary.get("failed", 0) > 0 or summary.get("aborted"):
        sys.exit(1)


def cmd_daily(queue_date: str | None, max_items: int | None,
              limit: int | None, source: str | None,
              min_wait: float | None, max_wait: float | None):
    """plan + execute を一括実行"""
    from planner.daily_planner import generate_daily_plan, format_plan_report
    from planner.queue_executor import QueueExecutor

    # --- Step 1: 計画生成 ---
    logger.info("=== daily: Step 1 - 投稿計画生成 ===")
    plan = generate_daily_plan(
        queue_date=queue_date,
        max_items=max_items,
        source_path=source,
    )
    print(format_plan_report(plan))

    if plan["planned"] == 0:
        print("登録対象が0件のため終了します。")
        return

    # --- Step 2: キュー実行 ---
    logger.info("=== daily: Step 2 - キュー実行 ===")
    executor = QueueExecutor(
        queue_date=queue_date,
        limit=limit,
        min_wait=min_wait,
        max_wait=max_wait,
    )
    summary = executor.run()

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\nログ: {config.LOG_DIR / f'{today}.log'}")

    if summary.get("failed", 0) > 0 or summary.get("aborted"):
        sys.exit(1)


def cmd_queue_status(queue_date: str | None):
    """キュー状況を表示する"""
    from planner.queue_manager import QueueManager

    qm = QueueManager()
    print(qm.format_status(queue_date))

    # 詳細表示
    items = qm.get_by_date(queue_date)
    if items:
        print(f"\n--- キュー詳細 ---")
        for item in items:
            status_icon = {"queued": "⏳", "running": "🔄", "posted": "✅",
                          "failed": "❌", "skipped": "⏭️"}.get(item["status"], "?")
            print(f"  {status_icon} [{item['status']:8s}] {item['title'][:40]}")
            if item["result_message"]:
                print(f"     → {item['result_message'][:60]}")
    qm.close()


def cmd_like(limit: int | None):
    """いいねBOTを実行する"""
    from executor.like_executor import LikeExecutor

    logger.info("=== いいねモード ===")
    executor = LikeExecutor(limit=limit)
    summary = executor.run()

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\nログ: {config.LOG_DIR / f'{today}.log'}")
    print(f"スクリーンショット: {config.SCREENSHOT_DIR / today}/")

    if summary.get("aborted"):
        sys.exit(1)


def cmd_follow(limit: int | None):
    """フォローBOTを実行する"""
    from executor.follow_executor import FollowExecutor

    logger.info("=== フォローモード ===")
    executor = FollowExecutor(limit=limit)
    summary = executor.run()

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\nログ: {config.LOG_DIR / f'{today}.log'}")
    print(f"スクリーンショット: {config.SCREENSHOT_DIR / today}/")

    if summary.get("aborted"):
        sys.exit(1)


def cmd_room(args):
    """CEOコマンド: room on/off/status/plus/generate-month"""
    from executor.room_controller import RoomController

    room_action = args.room_action

    if room_action == "on":
        RoomController.set_enabled(True, changed_by="cli")
        print("ROOM運用: ON に切り替えました。")

    elif room_action == "off":
        RoomController.set_enabled(False, changed_by="cli")
        print("ROOM運用: OFF に切り替えました。")
        print("  ※ 実行中のジョブは安全に完了します")
        print("  ※ room plus は引き続き実行可能です")

    elif room_action == "status":
        print(RoomController.get_status())

    elif room_action == "plus":
        plus_action = args.plus_action
        plus_count = args.plus_count
        tone = getattr(args, "tone", "pickup")
        force = getattr(args, "force", False)

        if plus_action not in ("post", "like", "follow"):
            print(f"エラー: 未知のアクション '{plus_action}'")
            print("  使用可能: post, like, follow")
            sys.exit(1)

        print(f"=== room plus {plus_action} {plus_count} (tone={tone}) ===")
        result = RoomController.exec_plus(
            action=plus_action,
            count=plus_count,
            tone=tone,
            force=force,
        )

        print(f"\n結果:")
        print(f"  要求:   {result.get('requested', 0)}件")
        print(f"  完了:   {result.get('completed', 0)}件")
        print(f"  失敗:   {result.get('failed', 0)}件")

    elif room_action == "generate-month":
        month = getattr(args, "month", None)
        schedule = RoomController.generate_monthly_schedule(month)
        print(RoomController.format_monthly_schedule(schedule))

    else:
        print(f"未知のroomコマンド: {room_action}")
        print("  使用可能: on, off, status, plus, generate-month")


def cmd_schedule(show: bool, generate: bool, target_date: str | None):
    """日次スケジュールの表示・生成"""
    from planner.daily_schedule import (
        generate_daily_schedule, load_daily_plan, format_schedule_report,
    )

    if generate:
        date = target_date or datetime.now().strftime("%Y-%m-%d")
        plan = generate_daily_schedule(date)
        print(format_schedule_report(plan))
        print(f"\n保存先: {config.DATA_DIR / 'daily_plan.json'}")
    elif show:
        plan = load_daily_plan()
        if plan:
            print(format_schedule_report(plan))
        else:
            print("daily_plan.json が見つかりません。")
            print("  python run.py schedule --generate で生成してください。")
    else:
        # デフォルト: 表示
        plan = load_daily_plan()
        if plan:
            print(format_schedule_report(plan))
        else:
            print("daily_plan.json が見つかりません。")
            print("  python run.py schedule --generate で生成してください。")


def cmd_preview(file_path: str | None, use_stdin: bool, count: int | None):
    """投稿プレビュー（dry-run: 投稿せずに内容を確認 + コメント候補3案 + スコア表示）"""
    from urllib.parse import quote
    from executor.comment_generator import (
        detect_genre, generate_comment_candidates,
    )
    from executor.post_scorer import score_comment

    # データ読み込み
    if use_stdin:
        try:
            raw = sys.stdin.read()
            data = json.loads(raw)
            if isinstance(data, list):
                posts = data
            elif isinstance(data, dict) and "posts" in data:
                posts = data["posts"]
            else:
                print("エラー: JSONの形式が不正です。")
                sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"エラー: JSONパース失敗: {e}")
            sys.exit(1)
    else:
        try:
            posts = load_posts_json(file_path)
        except (FileNotFoundError, ValueError) as e:
            print(f"エラー: {e}")
            sys.exit(1)

    if not posts:
        print("エラー: 投稿データが0件です")
        sys.exit(1)

    target_posts = posts[:count] if count else posts

    print("\n" + "=" * 60)
    print(f"投稿プレビュー (dry-run) - 120点ROOM運用BOT")
    print(f"全{len(posts)}件中 {len(target_posts)}件を表示")
    print("=" * 60)

    for i, post in enumerate(target_posts):
        num = i + 1
        title = post.get("title", "(タイトルなし)")
        url = post.get("url", "")
        image = post.get("image", "")
        comment = post.get("comment", "")
        collect_url = f"https://room.rakuten.co.jp/collect?url={quote(url, safe='')}" if url else "(URLなし)"
        genre = detect_genre(title, url, comment)

        print(f"\n{'=' * 60}")
        print(f"[{num}/{len(target_posts)}] {title}")
        print(f"{'=' * 60}")
        print(f"  url:         {url}")
        print(f"  collect_url: {collect_url}")
        print(f"  image:       {image if image else '(なし)'}")
        print(f"  genre:       {genre}")

        # バリデーション
        warnings = []
        if not url:
            warnings.append("URLが空です")
        elif not url.startswith("http"):
            warnings.append(f"URLが不正です: {url[:30]}")
        if warnings:
            for w in warnings:
                print(f"  [!] {w}")

        # --- 現在のコメント ---
        if comment:
            print(f"\n  [現在のcomment] ({len(comment)}文字):")
            for line in comment.split("\n"):
                print(f"    {line}")
            # 現在のコメントもスコアリング
            cur_score = score_comment(comment, genre)
            print(f"    -> score: {cur_score['score']}点 ({'PASS' if cur_score['pass'] else 'FAIL'})")
        else:
            print(f"\n  [現在のcomment] (なし - 自動生成されます)")

        # --- 生成コメント候補 3案 + スコア ---
        print(f"\n  [生成候補]")
        candidates = generate_comment_candidates(title, url, genre, count=3)
        for j, cand in enumerate(candidates):
            label = chr(65 + j)  # A, B, C
            sc = score_comment(cand["comment"], genre)
            mark = "PASS" if sc["pass"] else "FAIL"
            print(f"\n  --- 候補{label}: {cand['pattern']} | score: {sc['score']}点 ({mark}) ---")
            for line in cand["comment"].split("\n"):
                print(f"    {line}")
            print(f"    (genre: {cand['genre']}, {cand['char_count']}文字, tags: {cand['tags']})")

    print("\n" + "=" * 60)
    print(f"合計: {len(target_posts)}件")
    valid = sum(1 for p in target_posts
                if p.get("url", "").startswith("http"))
    print(f"投稿可能: {valid}件")
    print(f"問題あり: {len(target_posts) - valid}件")
    print("=" * 60)
    print("\n投稿を実行するには:")
    print(f'  python run.py batch --file {file_path or "posts.json"} --count {len(target_posts)}')


def main():
    parser = argparse.ArgumentParser(
        description="ROOM BOT v2 - 楽天ROOM自動投稿ボット（MVP）"
    )
    subparsers = parser.add_subparsers(dest="command", help="実行コマンド")

    # login コマンド
    subparsers.add_parser("login", help="楽天ROOMに手動ログインしてセッションを保存する")

    # post コマンド
    post_parser = subparsers.add_parser("post", help="1件の投稿を自動実行する")
    post_parser.add_argument("--url", required=True, help="楽天市場の商品ページURL")

    text_group = post_parser.add_mutually_exclusive_group(required=True)
    text_group.add_argument("--text", help="投稿するレビュー文（ハッシュタグ含む）")
    text_group.add_argument("--file", help="投稿文が書かれたテキストファイルのパス")

    # batch コマンド
    batch_parser = subparsers.add_parser("batch", help="JSONファイルから複数件をバッチ投稿する")
    batch_input = batch_parser.add_mutually_exclusive_group(required=True)
    batch_input.add_argument("--file", help="投稿データのJSONファイル")
    batch_input.add_argument("--stdin", action="store_true", help="標準入力からJSONを読み込む（n8n連携用）")
    batch_parser.add_argument("--count", type=int, default=None, help="投稿件数の上限（省略時は全件）")
    batch_parser.add_argument("--min-wait", type=float, default=None, help="投稿間隔の最小秒数（テスト用: 例 1）")
    batch_parser.add_argument("--max-wait", type=float, default=None, help="投稿間隔の最大秒数（テスト用: 例 3）")

    # preview コマンド
    preview_parser = subparsers.add_parser("preview", help="投稿内容をプレビュー表示する（dry-run）")
    preview_input = preview_parser.add_mutually_exclusive_group(required=True)
    preview_input.add_argument("--file", help="投稿データのJSONファイル")
    preview_input.add_argument("--stdin", action="store_true", help="標準入力からJSONを読み込む")
    preview_parser.add_argument("--count", type=int, default=None, help="表示件数の上限（省略時は全件）")

    # check-collect コマンド
    cc_parser = subparsers.add_parser("check-collect", help="商品URLがROOM collectに対応しているか検証する")
    cc_input = cc_parser.add_mutually_exclusive_group(required=True)
    cc_input.add_argument("--file", help="投稿データのJSONファイル")
    cc_input.add_argument("--stdin", action="store_true", help="標準入力からJSONを読み込む")
    cc_parser.add_argument("--count", type=int, default=None, help="チェック件数の上限（省略時は全件）")

    # investigate コマンド
    inv_parser = subparsers.add_parser("investigate", help="ROOM投稿導線を調査する（導線調査モード）")
    inv_input = inv_parser.add_mutually_exclusive_group(required=False)
    inv_input.add_argument("--file", help="商品URLのJSONファイル（省略時はROOM内部のみ調査）")
    inv_input.add_argument("--stdin", action="store_true", help="標準入力からJSONを読み込む")
    inv_parser.add_argument("--count", type=int, default=None, help="調査する商品数の上限（デフォルト3件）")

    # plan コマンド
    plan_parser = subparsers.add_parser("plan", help="当日の投稿計画を生成する")
    plan_parser.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD, 省略時は今日)")
    plan_parser.add_argument("--max-items", type=int, default=None, help="最大投稿件数（省略時はconfig値）")
    plan_parser.add_argument("--source", default=None, help="商品候補JSONのパス（省略時はsource_items.json）")

    # execute コマンド
    exec_parser = subparsers.add_parser("execute", help="SQLiteキューから投稿を実行する")
    exec_parser.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD, 省略時は今日)")
    exec_parser.add_argument("--limit", type=int, default=None, help="実行件数の上限")
    exec_parser.add_argument("--min-wait", type=float, default=None, help="投稿間隔の最小秒数")
    exec_parser.add_argument("--max-wait", type=float, default=None, help="投稿間隔の最大秒数")

    # daily コマンド
    daily_parser = subparsers.add_parser("daily", help="plan + execute を一括実行する")
    daily_parser.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD, 省略時は今日)")
    daily_parser.add_argument("--max-items", type=int, default=None, help="最大投稿件数")
    daily_parser.add_argument("--limit", type=int, default=None, help="実行件数の上限")
    daily_parser.add_argument("--source", default=None, help="商品候補JSONのパス")
    daily_parser.add_argument("--min-wait", type=float, default=None, help="投稿間隔の最小秒数")
    daily_parser.add_argument("--max-wait", type=float, default=None, help="投稿間隔の最大秒数")

    # queue-status コマンド
    qs_parser = subparsers.add_parser("queue-status", help="キュー状況を表示する")
    qs_parser.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD, 省略時は今日)")

    # like コマンド
    like_parser = subparsers.add_parser("like", help="いいねBOTを実行する")
    like_parser.add_argument("--limit", type=int, default=None,
                             help="いいね件数の上限（省略時はconfig値でランダム）")

    # follow コマンド
    follow_parser = subparsers.add_parser("follow", help="フォローBOTを実行する")
    follow_parser.add_argument("--limit", type=int, default=None,
                               help="フォロー件数の上限（省略時は30件）")

    # room コマンド（CEOコマンド）
    room_parser = subparsers.add_parser("room", help="CEO運用コマンド (on/off/status/plus)")
    room_parser.add_argument("room_action", nargs="?", default="status",
                              help="on / off / status / plus / generate-month")
    room_parser.add_argument("plus_action", nargs="?", default=None,
                              help="plusの場合: post / like / follow")
    room_parser.add_argument("plus_count", nargs="?", type=int, default=None,
                              help="plusの場合: 件数")
    room_parser.add_argument("--tone", default="pickup",
                              help="投稿トーン (normal / pickup, デフォルト: pickup)")
    room_parser.add_argument("--month", default=None,
                              help="generate-month用: YYYY-MM (省略時は今月)")
    room_parser.add_argument("--force", action="store_true",
                              help="OFF中でも強制実行")

    # schedule コマンド
    sched_parser = subparsers.add_parser("schedule", help="日次スケジュールの表示・生成")
    sched_parser.add_argument("--show", action="store_true", help="現在のスケジュールを表示")
    sched_parser.add_argument("--generate", action="store_true", help="スケジュールを生成")
    sched_parser.add_argument("--date", default=None, help="対象日 (YYYY-MM-DD, 省略時は今日)")

    args = parser.parse_args()

    if args.command == "login":
        cmd_login()
    elif args.command == "post":
        # 投稿文の取得
        if args.file:
            file_path = Path(args.file)
            if not file_path.exists():
                print(f"エラー: ファイルが見つかりません: {file_path}")
                sys.exit(1)
            review_text = file_path.read_text(encoding="utf-8").strip()
        else:
            review_text = args.text

        if not review_text:
            print("エラー: 投稿文が空です")
            sys.exit(1)

        cmd_post(args.url, review_text)
    elif args.command == "batch":
        cmd_batch(args.file, args.stdin, args.count,
                 min_wait=args.min_wait, max_wait=args.max_wait)
    elif args.command == "preview":
        cmd_preview(args.file, args.stdin, args.count)
    elif args.command == "check-collect":
        cmd_check_collect(args.file, args.stdin, args.count)
    elif args.command == "investigate":
        cmd_investigate(getattr(args, 'file', None), getattr(args, 'stdin', False), args.count)
    elif args.command == "plan":
        cmd_plan(args.date, args.max_items, args.source)
    elif args.command == "execute":
        cmd_execute(args.date, args.limit,
                    min_wait=args.min_wait, max_wait=args.max_wait)
    elif args.command == "daily":
        cmd_daily(args.date, args.max_items, args.limit, args.source,
                  min_wait=args.min_wait, max_wait=args.max_wait)
    elif args.command == "queue-status":
        cmd_queue_status(args.date)
    elif args.command == "like":
        cmd_like(args.limit)
    elif args.command == "follow":
        cmd_follow(args.limit)
    elif args.command == "room":
        cmd_room(args)
    elif args.command == "schedule":
        cmd_schedule(args.show, args.generate, args.date)
    else:
        parser.print_help()
        print("\n使用例:")
        print("  python run.py login                             # ログイン")
        print("  python run.py preview --file posts.json         # プレビュー")
        print("  python run.py batch --file posts.json           # バッチ投稿")
        print("  python run.py plan                              # 投稿計画生成")
        print("  python run.py execute --limit 35 --min-wait 30  # バッチ実行")
        print("  python run.py daily                             # plan+execute一括")
        print("  python run.py queue-status                      # 状況確認")
        print("  python run.py like                              # いいねBOT実行")
        print("  python run.py like --limit 10                   # 10件いいね")
        print("  python run.py follow                            # フォローBOT実行")
        print("  python run.py follow --limit 20                 # 20件フォロー")
        print("  python run.py room on                           # ROOM運用ON")
        print("  python run.py room off                          # ROOM運用OFF")
        print("  python run.py room status                       # 状態確認")
        print("  python run.py room plus like 10                 # 臨時いいね10件")
        print("  python run.py room plus follow 20               # 臨時フォロー20件")
        print("  python run.py room generate-month               # 月間スケジュール生成")
        print("  python run.py schedule --show                   # スケジュール表示")
        print("  python run.py schedule --generate               # スケジュール生成")


if __name__ == "__main__":
    main()
