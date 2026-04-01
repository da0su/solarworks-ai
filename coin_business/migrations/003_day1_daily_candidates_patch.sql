-- 003_day1_daily_candidates_patch.sql
-- daily_candidates への最小限カラム追加（既存カラムは保持）

alter table daily_candidates
  add column if not exists decision_status text;

alter table daily_candidates
  add column if not exists decision_last_updated_at timestamptz;

alter table daily_candidates
  add column if not exists decision_reason_code text;

alter table daily_candidates
  add column if not exists ceo_decision_note text;

alter table daily_candidates
  add column if not exists last_status_checked_at timestamptz;

alter table daily_candidates
  add column if not exists recommended_max_bid_jpy numeric(18,2);

alter table daily_candidates
  add column if not exists projected_profit_jpy numeric(18,2);

alter table daily_candidates
  add column if not exists projected_roi numeric(18,4);

alter table daily_candidates
  add column if not exists evidence_count integer not null default 0;

alter table daily_candidates
  add column if not exists shipping_from_country text;

alter table daily_candidates
  add column if not exists source_currency text;

alter table daily_candidates
  add column if not exists comparison_quality_score numeric(18,4);

alter table daily_candidates
  add column if not exists recency_bucket_summary jsonb not null default '{}'::jsonb;

create index if not exists idx_daily_candidates_decision_status
  on daily_candidates(decision_status);

create index if not exists idx_daily_candidates_last_status_checked_at
  on daily_candidates(last_status_checked_at desc);

create index if not exists idx_daily_candidates_projected_profit_jpy
  on daily_candidates(projected_profit_jpy desc);
