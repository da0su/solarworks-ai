"""ROOM BOT v2 - SQLite キュー管理

投稿キューをSQLiteで管理する。
ステータス遷移: queued → running → posted / failed / skipped
item_code重複防止、失敗理由記録、再開可能。
"""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import config

DB_PATH = config.DATA_DIR / "room_bot.db"

# ステータス定数
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_POSTED = "posted"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


class QueueManager:
    """SQLite ベースの投稿キュー管理"""

    def __init__(self, db_path: Path | str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._init_db()

    # ================================================================
    # DB初期化
    # ================================================================

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS post_queue (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                queue_date      TEXT NOT NULL,
                item_code       TEXT NOT NULL,
                item_url        TEXT NOT NULL,
                title           TEXT NOT NULL DEFAULT '',
                comment         TEXT NOT NULL DEFAULT '',
                genre           TEXT NOT NULL DEFAULT '',
                score           INTEGER NOT NULL DEFAULT 0,
                scheduled_at    TEXT,
                status          TEXT NOT NULL DEFAULT 'queued',
                result_message  TEXT DEFAULT '',
                error_type      TEXT DEFAULT '',
                room_url        TEXT DEFAULT '',
                attempt_count   INTEGER NOT NULL DEFAULT 0,
                posted_at       TEXT,
                created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_queue_date_status
                ON post_queue(queue_date, status);

            CREATE INDEX IF NOT EXISTS idx_item_code
                ON post_queue(item_code);

            CREATE TABLE IF NOT EXISTS daily_summary (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_date    TEXT NOT NULL UNIQUE,
                planned         INTEGER NOT NULL DEFAULT 0,
                posted          INTEGER NOT NULL DEFAULT 0,
                failed          INTEGER NOT NULL DEFAULT 0,
                skipped         INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                updated_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );
        """)
        conn.commit()

    # ================================================================
    # キュー追加
    # ================================================================

    def enqueue(self, queue_date: str, item_code: str, item_url: str,
                title: str = "", comment: str = "", genre: str = "",
                score: int = 0, scheduled_at: str = None) -> int | None:
        """キューに1件追加。item_code重複時はNone返却（スキップ）"""
        conn = self._get_conn()

        # item_code重複チェック（全期間で同じitemは投稿しない）
        existing = conn.execute(
            "SELECT id, status FROM post_queue WHERE item_code = ? AND status = 'posted'",
            (item_code,)
        ).fetchone()
        if existing:
            return None  # 投稿済み

        # 同日の同item_codeはスキップ
        same_day = conn.execute(
            "SELECT id FROM post_queue WHERE queue_date = ? AND item_code = ?",
            (queue_date, item_code)
        ).fetchone()
        if same_day:
            return None

        cursor = conn.execute("""
            INSERT INTO post_queue
                (queue_date, item_code, item_url, title, comment, genre, score, scheduled_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (queue_date, item_code, item_url, title, comment, genre, score, scheduled_at))
        conn.commit()
        return cursor.lastrowid

    def enqueue_batch(self, queue_date: str, items: list[dict]) -> dict:
        """複数件をまとめてキュー追加"""
        added = 0
        skipped = 0
        for item in items:
            result = self.enqueue(
                queue_date=queue_date,
                item_code=item.get("item_code", ""),
                item_url=item.get("item_url", item.get("url", "")),
                title=item.get("title", ""),
                comment=item.get("comment", ""),
                genre=item.get("genre", ""),
                score=item.get("score", 0),
                scheduled_at=item.get("scheduled_at"),
            )
            if result is not None:
                added += 1
            else:
                skipped += 1
        return {"added": added, "skipped_duplicate": skipped}

    # ================================================================
    # キュー取得
    # ================================================================

    def get_pending(self, queue_date: str = None, limit: int = None) -> list[dict]:
        """実行待ち(queued)の投稿を取得"""
        date = queue_date or datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        sql = """
            SELECT * FROM post_queue
            WHERE queue_date = ? AND status = 'queued'
            ORDER BY scheduled_at ASC, id ASC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = conn.execute(sql, (date,)).fetchall()
        return [dict(r) for r in rows]

    def acquire_next(self, queue_date: str = None, skip_ids: set = None) -> dict | None:
        """キューから1件を原子的に取得し running に遷移する。

        SELECT + UPDATE を単一トランザクション内で実行し、
        並行する別プロセスとの二重取得を防止する。
        取得できなければ None を返す。
        """
        date = queue_date or datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            sql = """
                SELECT * FROM post_queue
                WHERE queue_date = ? AND status = 'queued'
                ORDER BY scheduled_at ASC, id ASC
            """
            rows = conn.execute(sql, (date,)).fetchall()
            # skip_ids で既に処理済み/スキップ済みの id を除外
            for row in rows:
                if skip_ids and row["id"] in skip_ids:
                    continue
                queue_id = row["id"]
                cursor = conn.execute("""
                    UPDATE post_queue SET
                        status = 'running',
                        attempt_count = attempt_count + 1,
                        updated_at = datetime('now','localtime')
                    WHERE id = ? AND status = 'queued'
                """, (queue_id,))
                if cursor.rowcount > 0:
                    conn.commit()
                    return dict(row)
            conn.commit()
            return None
        except Exception:
            conn.rollback()
            raise

    def get_by_date(self, queue_date: str = None) -> list[dict]:
        """指定日の全キューを取得"""
        date = queue_date or datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM post_queue WHERE queue_date = ? ORDER BY id ASC",
            (date,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_status_summary(self, queue_date: str = None) -> dict:
        """指定日のステータス集計"""
        date = queue_date or datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        rows = conn.execute("""
            SELECT status, COUNT(*) as cnt
            FROM post_queue WHERE queue_date = ?
            GROUP BY status
        """, (date,)).fetchall()
        summary = {STATUS_QUEUED: 0, STATUS_RUNNING: 0,
                   STATUS_POSTED: 0, STATUS_FAILED: 0, STATUS_SKIPPED: 0}
        for r in rows:
            summary[r["status"]] = r["cnt"]
        summary["total"] = sum(summary.values())
        summary["date"] = date
        return summary

    # ================================================================
    # ステータス更新
    # ================================================================

    def mark_running(self, queue_id: int) -> bool:
        """queued → running"""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE post_queue SET
                status = 'running',
                attempt_count = attempt_count + 1,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND status IN ('queued', 'failed')
        """, (queue_id,))
        conn.commit()
        return cursor.rowcount > 0

    def mark_posted(self, queue_id: int, room_url: str = "",
                    result_message: str = "") -> bool:
        """running → posted"""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE post_queue SET
                status = 'posted',
                room_url = ?,
                result_message = ?,
                posted_at = datetime('now','localtime'),
                updated_at = datetime('now','localtime')
            WHERE id = ? AND status = 'running'
        """, (room_url, result_message, queue_id))
        conn.commit()
        return cursor.rowcount > 0

    def mark_failed(self, queue_id: int, error_type: str = "",
                    result_message: str = "") -> bool:
        """running → failed"""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE post_queue SET
                status = 'failed',
                error_type = ?,
                result_message = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND status = 'running'
        """, (error_type, result_message, queue_id))
        conn.commit()
        return cursor.rowcount > 0

    def mark_skipped(self, queue_id: int, reason: str = "") -> bool:
        """running → skipped"""
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE post_queue SET
                status = 'skipped',
                result_message = ?,
                updated_at = datetime('now','localtime')
            WHERE id = ? AND status IN ('running', 'queued')
        """, (reason, queue_id))
        conn.commit()
        return cursor.rowcount > 0

    def reset_running(self, queue_date: str = None):
        """異常終了で running のまま残ったレコードを queued に戻す"""
        date = queue_date or datetime.now().strftime("%Y-%m-%d")
        conn = self._get_conn()
        cursor = conn.execute("""
            UPDATE post_queue SET
                status = 'queued',
                updated_at = datetime('now','localtime')
            WHERE queue_date = ? AND status = 'running'
        """, (date,))
        conn.commit()
        return cursor.rowcount

    # ================================================================
    # 重複チェック
    # ================================================================

    def is_item_posted(self, item_code: str) -> bool:
        """item_codeが過去に投稿済みかチェック"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id FROM post_queue WHERE item_code = ? AND status = 'posted' LIMIT 1",
            (item_code,)
        ).fetchone()
        return row is not None

    def get_posted_item_codes(self) -> set[str]:
        """投稿済みの全item_codeをセットで返す"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT item_code FROM post_queue WHERE status = 'posted'"
        ).fetchall()
        return {r["item_code"] for r in rows}

    # ================================================================
    # デイリーサマリー
    # ================================================================

    def save_daily_summary(self, queue_date: str = None):
        """その日の集計をdaily_summaryに保存"""
        date = queue_date or datetime.now().strftime("%Y-%m-%d")
        stats = self.get_status_summary(date)
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO daily_summary (summary_date, planned, posted, failed, skipped, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(summary_date) DO UPDATE SET
                planned = excluded.planned,
                posted = excluded.posted,
                failed = excluded.failed,
                skipped = excluded.skipped,
                updated_at = datetime('now','localtime')
        """, (date, stats["total"], stats[STATUS_POSTED],
              stats[STATUS_FAILED], stats[STATUS_SKIPPED]))
        conn.commit()

    # ================================================================
    # ユーティリティ
    # ================================================================

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def format_status(self, queue_date: str = None) -> str:
        """キュー状況を人間が読める形式で返す"""
        stats = self.get_status_summary(queue_date)
        date = stats["date"]
        lines = [
            f"=== キュー状況 ({date}) ===",
            f"  合計:     {stats['total']}件",
            f"  待機:     {stats[STATUS_QUEUED]}件",
            f"  実行中:   {stats[STATUS_RUNNING]}件",
            f"  成功:     {stats[STATUS_POSTED]}件",
            f"  失敗:     {stats[STATUS_FAILED]}件",
            f"  スキップ: {stats[STATUS_SKIPPED]}件",
        ]
        return "\n".join(lines)
