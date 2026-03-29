"""
SEO キーワード・記事キュー管理 (SQLite)

Tables:
  keywords  — キーワードマスタ（重複防止・優先度管理）
  articles  — 生成済み記事キュー（投稿待ち/投稿済み/失敗）
  daily_log — 日次実績ログ
"""

import sqlite3
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "seo.db"

# ── 初期キーワードシード ─────────────────────────────────────────────
# keyword_proposal_v1.md Phase 1-2 対応
KEYWORD_SEEDS = [
    # Priority S（最優先）
    {"keyword": "V2H 補助金 2026 申請方法",           "theme": "V2H",   "type": "行動系", "priority": 100},
    {"keyword": "太陽光発電 補助金 2026 最新",         "theme": "太陽光", "type": "行動系", "priority": 99},
    # Priority A
    {"keyword": "蓄電池 卒FIT おすすめ 2026",          "theme": "蓄電池", "type": "比較系", "priority": 90},
    {"keyword": "V2H おすすめ メーカー 比較 2026",     "theme": "V2H",   "type": "比較系", "priority": 89},
    {"keyword": "V2H 設置費用 相場 2026",              "theme": "V2H",   "type": "不安系", "priority": 85},
    {"keyword": "蓄電池 初期費用 相場 2026",           "theme": "蓄電池", "type": "不安系", "priority": 84},
    # Priority B
    {"keyword": "V2H 対応車種 一覧 2026",              "theme": "V2H",   "type": "比較系", "priority": 75},
    {"keyword": "蓄電池 卒FIT シミュレーション",       "theme": "蓄電池", "type": "比較系", "priority": 74},
    {"keyword": "太陽光発電 デメリット 後悔しない",    "theme": "太陽光", "type": "不安系", "priority": 70},
    {"keyword": "V2H ニチコン 評判 2026",              "theme": "V2H",   "type": "比較系", "priority": 69},
    # 追加キーワード（既存GASシートから）
    {"keyword": "蓄電池 おすすめ メーカー 比較 2026",  "theme": "蓄電池", "type": "比較系", "priority": 65},
    {"keyword": "太陽光発電 おすすめ 比較 2026",       "theme": "太陽光", "type": "比較系", "priority": 60},
    {"keyword": "家庭用蓄電池 価格 相場 2026",         "theme": "蓄電池", "type": "不安系", "priority": 58},
    {"keyword": "V2H 補助金 申請 条件 2026",           "theme": "V2H",   "type": "行動系", "priority": 55},
    {"keyword": "太陽光発電 蓄電池 セット おすすめ",   "theme": "太陽光", "type": "比較系", "priority": 52},
    {"keyword": "V2H パナソニック 評判 価格",          "theme": "V2H",   "type": "比較系", "priority": 50},
    {"keyword": "卒FIT 売電 蓄電池 どちらが得",        "theme": "蓄電池", "type": "不安系", "priority": 48},
    {"keyword": "太陽光発電 補助金 申請方法 2026",     "theme": "太陽光", "type": "行動系", "priority": 47},
    {"keyword": "V2H リース 費用 比較",                "theme": "V2H",   "type": "不安系", "priority": 45},
    {"keyword": "蓄電池 寿命 交換 費用",               "theme": "蓄電池", "type": "不安系", "priority": 43},
    {"keyword": "太陽光発電 後悔 失敗 理由",           "theme": "太陽光", "type": "不安系", "priority": 40},
    {"keyword": "V2H 電気代 節約 シミュレーション",    "theme": "V2H",   "type": "比較系", "priority": 38},
    {"keyword": "蓄電池 補助金 2026 申請",             "theme": "蓄電池", "type": "行動系", "priority": 95},
    {"keyword": "V2H 補助金 2026 金額 上限",           "theme": "V2H",   "type": "行動系", "priority": 97},
    {"keyword": "太陽光発電 売電 価格 2026",           "theme": "太陽光", "type": "不安系", "priority": 35},
    {"keyword": "V2H 中古 おすすめ 注意点",            "theme": "V2H",   "type": "不安系", "priority": 30},
    {"keyword": "蓄電池 メーカー ランキング 2026",     "theme": "蓄電池", "type": "比較系", "priority": 62},
    {"keyword": "太陽光発電 業者 選び方 注意点",       "theme": "太陽光", "type": "不安系", "priority": 28},
    {"keyword": "V2H 工事費 相場 業者",                "theme": "V2H",   "type": "不安系", "priority": 42},
    {"keyword": "蓄電池 太陽光 補助金 組み合わせ 2026","theme": "蓄電池", "type": "行動系", "priority": 80},
]


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """DB初期化・テーブル作成・キーワードシード投入"""
    conn = _get_conn()
    cur = conn.cursor()

    cur.executescript("""
        CREATE TABLE IF NOT EXISTS keywords (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword     TEXT    NOT NULL UNIQUE,
            theme       TEXT    NOT NULL,
            type        TEXT    NOT NULL,
            priority    INTEGER NOT NULL DEFAULT 50,
            status      TEXT    NOT NULL DEFAULT 'pending',  -- pending / used / skip
            used_at     TEXT,
            created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS articles (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword_id   INTEGER NOT NULL,
            keyword      TEXT    NOT NULL,
            title        TEXT    NOT NULL,
            content      TEXT    NOT NULL,
            status       TEXT    NOT NULL DEFAULT 'queued',  -- queued / posting / posted / failed
            wp_post_id   INTEGER,
            wp_url       TEXT,
            posted_at    TEXT,
            retry_count  INTEGER NOT NULL DEFAULT 0,
            error_msg    TEXT,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS daily_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date     TEXT    NOT NULL,
            posted_count INTEGER NOT NULL DEFAULT 0,
            failed_count INTEGER NOT NULL DEFAULT 0,
            generated    INTEGER NOT NULL DEFAULT 0,
            slack_sent   INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
        );
    """)

    # キーワードシード（INSERT OR IGNORE で重複スキップ）
    for seed in KEYWORD_SEEDS:
        cur.execute("""
            INSERT OR IGNORE INTO keywords (keyword, theme, type, priority)
            VALUES (?, ?, ?, ?)
        """, (seed["keyword"], seed["theme"], seed["type"], seed["priority"]))

    conn.commit()
    conn.close()


def get_queued_count() -> int:
    """キュー内の投稿待ち記事数を返す"""
    conn = _get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE status = 'queued'"
    ).fetchone()[0]
    conn.close()
    return count


def get_next_keyword() -> dict | None:
    """未使用キーワードを優先度順に1件取得"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT k.*
        FROM keywords k
        WHERE k.status = 'pending'
          AND k.keyword NOT IN (
              SELECT keyword FROM articles WHERE status != 'failed'
          )
        ORDER BY k.priority DESC, k.id ASC
        LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_keyword_used(keyword_id: int):
    """キーワードを使用済みにする"""
    conn = _get_conn()
    conn.execute("""
        UPDATE keywords SET status = 'used', used_at = datetime('now','localtime')
        WHERE id = ?
    """, (keyword_id,))
    conn.commit()
    conn.close()


def add_article_to_queue(keyword_id: int, keyword: str, title: str, content: str) -> int:
    """生成した記事をキューに追加。記事IDを返す"""
    conn = _get_conn()
    cur = conn.execute("""
        INSERT INTO articles (keyword_id, keyword, title, content)
        VALUES (?, ?, ?, ?)
    """, (keyword_id, keyword, title, content))
    article_id = cur.lastrowid
    conn.commit()
    conn.close()
    return article_id


def get_next_article_to_post() -> dict | None:
    """投稿待ちの最古の記事を1件取得"""
    conn = _get_conn()
    row = conn.execute("""
        SELECT * FROM articles
        WHERE status = 'queued'
        ORDER BY created_at ASC
        LIMIT 1
    """).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_article_posting(article_id: int):
    """記事を投稿中状態に更新"""
    conn = _get_conn()
    conn.execute("""
        UPDATE articles
        SET status = 'posting', updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (article_id,))
    conn.commit()
    conn.close()


def mark_article_posted(article_id: int, wp_post_id: int, wp_url: str):
    """記事を投稿完了に更新"""
    conn = _get_conn()
    conn.execute("""
        UPDATE articles
        SET status = 'posted',
            wp_post_id = ?,
            wp_url = ?,
            posted_at = datetime('now','localtime'),
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (wp_post_id, wp_url, article_id))
    conn.commit()
    conn.close()


def mark_article_failed(article_id: int, error_msg: str):
    """記事を失敗に更新（retry_countをインクリメント）"""
    conn = _get_conn()
    conn.execute("""
        UPDATE articles
        SET status = 'failed',
            retry_count = retry_count + 1,
            error_msg = ?,
            updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (error_msg, article_id))
    conn.commit()
    conn.close()


def requeue_article(article_id: int):
    """失敗した記事を再キューに戻す（retry_countは保持）"""
    conn = _get_conn()
    conn.execute("""
        UPDATE articles
        SET status = 'queued', updated_at = datetime('now','localtime')
        WHERE id = ?
    """, (article_id,))
    conn.commit()
    conn.close()


def get_retry_count(article_id: int) -> int:
    conn = _get_conn()
    row = conn.execute("SELECT retry_count FROM articles WHERE id = ?", (article_id,)).fetchone()
    conn.close()
    return row["retry_count"] if row else 0


def log_daily_result(log_date: str, posted: int, failed: int, generated: int, slack_sent: int = 0):
    """日次実績を記録（日付が同じならUPDATE）"""
    conn = _get_conn()
    existing = conn.execute("SELECT id FROM daily_log WHERE log_date = ?", (log_date,)).fetchone()
    if existing:
        conn.execute("""
            UPDATE daily_log
            SET posted_count = ?, failed_count = ?, generated = ?, slack_sent = ?
            WHERE log_date = ?
        """, (posted, failed, generated, slack_sent, log_date))
    else:
        conn.execute("""
            INSERT INTO daily_log (log_date, posted_count, failed_count, generated, slack_sent)
            VALUES (?, ?, ?, ?, ?)
        """, (log_date, posted, failed, generated, slack_sent))
    conn.commit()
    conn.close()


def get_today_posted_hours() -> list[int]:
    """本日投稿済みの時間帯リスト（重複投稿防止）"""
    today = date.today().isoformat()
    conn = _get_conn()
    rows = conn.execute("""
        SELECT posted_at FROM articles
        WHERE status = 'posted' AND posted_at LIKE ?
    """, (f"{today}%",)).fetchall()
    conn.close()
    hours = []
    for row in rows:
        if row["posted_at"]:
            try:
                h = int(row["posted_at"][11:13])
                hours.append(h)
            except Exception:
                pass
    return hours


def get_status_summary() -> dict:
    """現在のキュー状況サマリー"""
    conn = _get_conn()
    queued   = conn.execute("SELECT COUNT(*) FROM articles WHERE status='queued'").fetchone()[0]
    posting  = conn.execute("SELECT COUNT(*) FROM articles WHERE status='posting'").fetchone()[0]
    posted   = conn.execute("SELECT COUNT(*) FROM articles WHERE status='posted'").fetchone()[0]
    failed   = conn.execute("SELECT COUNT(*) FROM articles WHERE status='failed'").fetchone()[0]
    kw_pending = conn.execute("SELECT COUNT(*) FROM keywords WHERE status='pending'").fetchone()[0]
    kw_used  = conn.execute("SELECT COUNT(*) FROM keywords WHERE status='used'").fetchone()[0]
    conn.close()
    return {
        "queued": queued, "posting": posting, "posted": posted,
        "failed": failed, "kw_pending": kw_pending, "kw_used": kw_used,
    }


if __name__ == "__main__":
    init_db()
    s = get_status_summary()
    print("DB initialized.")
    print(f"  keywords pending={s['kw_pending']}, used={s['kw_used']}")
    print(f"  articles queued={s['queued']}, posted={s['posted']}, failed={s['failed']}")
