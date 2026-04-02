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
  1. yahoo_sold_sync       -- 毎日 06:00 JST  (Day 2 実装済み)
  2. yahoo_promoter        -- 毎日 06:30 JST  (Day 4 実装済み)
  3. seed_generator        -- 毎日 07:00 JST  (Day 4 実装済み)
  4. ebay_listings_sync    -- 毎日 10:00 JST  (Day 5 実装済み)
  5. ebay_seed_scanner_1   -- 毎日 08:00 JST  (Day 6 実装済み)
  6. ebay_seed_scanner_2   -- 毎日 14:00 JST  (Day 6 実装済み)
  7. ebay_seed_scanner_3   -- 毎日 20:00 JST  (Day 6 実装済み)
  8. global_auction_sync   -- 毎日 07:30 JST  (Day 7 実装済み)
  9. global_lot_ingest_1   -- 毎日 08:30 JST  (Day 7 実装済み)
 10. global_lot_ingest_2   -- 毎日 14:30 JST  (Day 7 実装済み)
 11. match_engine_daily    -- 毎日 09:00 JST  (Day 8 実装済み)
 12. cap_audit_daily       -- 毎日 09:30 JST  (Day 8 実装済み)
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
    # Day 5: eBay API 取り込み (Day 5 実装済み)
    # READY seed を使って eBay Browse API 検索 → ebay_listings_raw / snapshots 保存
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "ebay_listings_sync",
        description     = "eBay seed × listing マッチング → ebay_listings_raw + snapshots",
        script          = "ebay_api_ingest.py",
        cron_schedule   = "0 10 * * *",          # 毎日 10:00 JST
        cli_command     = "ebay-ingest",
        implemented_day = 5,
        args            = ["--limit", "50"],      # 1 日 50 seed まで処理
    ),

    # ----------------------------------------------------------------
    # Day 6: eBay seed scanner 1回目 (Day 6 実装済み)
    # READY seed を priority 降順でスキャン → ebay_seed_hits
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "ebay_seed_scanner_1",
        description     = "Yahoo seed 起点 eBay スキャン (1回目 08:00 JST)",
        script          = "ebay_seed_scanner.py",
        cron_schedule   = "0 8 * * *",           # 毎日 08:00 JST
        cli_command     = "ebay-scan",
        implemented_day = 6,
        args            = ["--limit", "50"],
    ),

    # ----------------------------------------------------------------
    # Day 6: eBay seed scanner 2回目 (Day 6 実装済み)
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "ebay_seed_scanner_2",
        description     = "Yahoo seed 起点 eBay スキャン (2回目 14:00 JST)",
        script          = "ebay_seed_scanner.py",
        cron_schedule   = "0 14 * * *",          # 毎日 14:00 JST
        cli_command     = "ebay-scan",
        implemented_day = 6,
        args            = ["--limit", "50"],
    ),

    # ----------------------------------------------------------------
    # Day 6: eBay seed scanner 3回目 (Day 6 実装済み)
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "ebay_seed_scanner_3",
        description     = "Yahoo seed 起点 eBay スキャン (3回目 20:00 JST)",
        script          = "ebay_seed_scanner.py",
        cron_schedule   = "0 20 * * *",          # 毎日 20:00 JST
        cli_command     = "ebay-scan",
        implemented_day = 6,
        args            = ["--limit", "50"],
    ),

    # ----------------------------------------------------------------
    # Day 7: 世界オークション event 同期 (Day 7 実装済み)
    # Heritage / Stack's Bowers / Spink / Noble のイベントを global_auction_events へ
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "global_auction_sync",
        description     = "世界オークション event を global_auction_events に同期",
        script          = "global_auction_sync.py",
        cron_schedule   = "30 7 * * *",           # 毎日 07:30 JST
        cli_command     = "global-sync",
        implemented_day = 7,
        args            = [],
    ),

    # ----------------------------------------------------------------
    # Day 7: 世界オークション lot 取り込み 1回目 (Day 7 実装済み)
    # T-minus cadence に基づいて lot を取得 → global_auction_lots + snapshots
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "global_lot_ingest_1",
        description     = "世界オークション lot 取り込み (1回目 08:30 JST)",
        script          = "global_lot_ingest.py",
        cron_schedule   = "30 8 * * *",           # 毎日 08:30 JST
        cli_command     = "global-ingest",
        implemented_day = 7,
        args            = [],
    ),

    # ----------------------------------------------------------------
    # Day 7: 世界オークション lot 取り込み 2回目 (Day 7 実装済み)
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "global_lot_ingest_2",
        description     = "世界オークション lot 取り込み (2回目 14:30 JST)",
        script          = "global_lot_ingest.py",
        cron_schedule   = "30 14 * * *",          # 毎日 14:30 JST
        cli_command     = "global-ingest",
        implemented_day = 7,
        args            = [],
    ),

    # ----------------------------------------------------------------
    # Day 8: match_engine (Day 8 実装済み)
    # eBay listing / global lot × Yahoo seed 照合 → candidate_match_results
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "match_engine_daily",
        description     = "eBay/global lot × Yahoo seed 照合 → Level A/B/C 判定",
        script          = "match_engine.py",
        cron_schedule   = "0 9 * * *",            # 毎日 09:00 JST
        cli_command     = "match-engine",
        implemented_day = 8,
        args            = ["--limit", "100"],
    ),

    # ----------------------------------------------------------------
    # Day 8: CAP audit runner (Day 8 実装済み)
    # Level A match に audit gate → AUDIT_PASS を daily_candidates に昇格
    # ----------------------------------------------------------------
    JobDef(
        job_id          = "cap_audit_daily",
        description     = "Level A match に audit gate → AUDIT_PASS を daily_candidates 昇格",
        script          = "cap_audit_runner.py",
        cron_schedule   = "30 9 * * *",           # 毎日 09:30 JST
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
