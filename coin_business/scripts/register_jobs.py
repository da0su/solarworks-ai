"""
coin_business/scripts/register_jobs.py
=========================================
Day 2〜10 で実装するジョブのスケジュール定義ファイル。

このファイルは「ジョブカタログ」であり、実行環境（Windows タスクスケジューラ /
cron / Supabase pg_cron）向けの設定情報を一覧する。

スケジュール実行方法:
  Windows タスクスケジューラの場合:
    - アクション: python <PROJECT>/coin_business/scripts/<script>.py
    - 作業ディレクトリ: <PROJECT>/coin_business

  Linux cron の場合:
    - crontab に CRON_SCHEDULE の値を貼り付ける

現在登録済みジョブ:
  1. yahoo_sold_sync     -- 毎日 06:00 JST  (Day 2 実装済み)
  2. yahoo_promoter      -- 毎日 06:30 JST  (Day 4 実装済み)
  3. seed_generator      -- 毎日 07:00 JST  (Day 4 実装済み)
"""

from __future__ import annotations

from dataclasses import dataclass

# ================================================================
# ジョブ定義
# ================================================================

@dataclass
class JobDef:
    job_id:          str
    description:     str
    script:          str          # coin_business/scripts/ 配下のスクリプト名
    cron_schedule:   str          # 5 field cron (JST)
    cli_command:     str          # run.py 経由のコマンド (オプション)
    implemented_day: int          # 実装日 (Day N)
    args:            list[str]    # デフォルト引数


JOBS: list[JobDef] = [

    # ----------------------------------------------------------------
    # Day 2: Yahoo staging 同期
    # 毎日 09:00 JST に market_transactions から yahoo_sold_lots_staging へ upsert
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "yahoo_sold_sync_daily",
        description     = "Yahoo!落札履歴を staging に同期 (PENDING_CEO で蓄積)",
        script          = "yahoo_sold_sync.py",
        cron_schedule   = "0 6 * * *",           # 毎日 06:00 JST
        cli_command     = "yahoo-sync",
        implemented_day = 2,
        args            = ["--new-only"],        # 差分のみ
    ),

    # ----------------------------------------------------------------
    # Day 4: Yahoo promoter (Day 4 実装済み)
    # CEO 承認済み (APPROVED_TO_MAIN) を本DB yahoo_sold_lots へ昇格
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "yahoo_promoter_daily",
        description     = "APPROVED_TO_MAIN → yahoo_sold_lots へ昇格",
        script          = "yahoo_promoter.py",
        cron_schedule   = "30 6 * * *",          # 毎日 06:30 JST
        cli_command     = "yahoo-promote",
        implemented_day = 4,
        args            = [],
    ),

    # ----------------------------------------------------------------
    # Day 4: Seed generator (Day 4 実装済み)
    # yahoo_sold_lots から探索用 seed を生成 → yahoo_coin_seeds へ
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "seed_generator_daily",
        description     = "yahoo_sold_lots → yahoo_coin_seeds に seed 生成",
        script          = "seed_generator.py",
        cron_schedule   = "0 7 * * *",           # 毎日 07:00 JST
        cli_command     = "seed-generate",
        implemented_day = 4,
        args            = ["--new-only"],         # 差分のみ
    ),

    # ----------------------------------------------------------------
    # Day 5/6: eBay API 取り込み (未実装)
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "ebay_listings_sync",
        description     = "eBay seed × listing マッチング → ebay_listings_raw",
        script          = "ebay_api_ingest.py",  # Day 5 実装予定
        cron_schedule   = "0 10 * * *",
        cli_command     = "ebay-ingest",
        implemented_day = 5,
        args            = [],
    ),

    # ----------------------------------------------------------------
    # Day 7: 世界オークション lot 取り込み (未実装)
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "global_auction_sync",
        description     = "T-minus 管理: global_auction_events / lots 同期",
        script          = "global_auction_sync.py",  # Day 7 実装予定
        cron_schedule   = "0 8 * * *",
        cli_command     = "global-sync",
        implemented_day = 7,
        args            = [],
    ),

    # ----------------------------------------------------------------
    # Day 8: match + CAP audit (未実装)
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "cap_audit_daily",
        description     = "match_engine → cap_audit_runner → daily_candidates 昇格",
        script          = "cap_audit_runner.py",  # Day 8 実装予定
        cron_schedule   = "0 11 * * *",
        cli_command     = "cap-audit",
        implemented_day = 8,
        args            = [],
    ),

    # ----------------------------------------------------------------
    # Day 10: 朝次 Slack 通知 (未実装)
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "morning_brief",
        description     = "毎朝8時: 前日の候補サマリーを Slack に送信",
        script          = "slack_notifier.py",  # Day 10 実装予定
        cron_schedule   = "0 8 * * *",
        cli_command     = "slack-brief",
        implemented_day = 10,
        args            = ["--type", "morning_brief"],
    ),
]


# ================================================================
# 確認用 CLI
# ================================================================

def print_job_schedule() -> None:
    """登録済みジョブの一覧を表示する。"""
    print("=" * 70)
    print("coin_business -- Job Schedule")
    print("=" * 70)
    print(f"  {'job_id':<30} {'cron':>12}  {'Day':>4}  {'status'}")
    print("-" * 70)
    for j in JOBS:
        status = "READY" if _is_script_implemented(j.script) else "pending"
        print(f"  {j.job_id:<30} {j.cron_schedule:>12}  D{j.implemented_day:02d}  {status}")
        print(f"    → {j.description}")
        if j.args:
            print(f"    args: {' '.join(j.args)}")
    print("=" * 70)


def _is_script_implemented(script_name: str) -> bool:
    """スクリプトが scripts/ ディレクトリに存在するか確認する。"""
    from pathlib import Path
    scripts_dir = Path(__file__).parent
    return (scripts_dir / script_name).exists()


if __name__ == "__main__":
    print_job_schedule()
