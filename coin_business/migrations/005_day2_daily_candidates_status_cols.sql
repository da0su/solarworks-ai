-- 005_day2_daily_candidates_status_cols.sql
-- status_refresher / eligibility_rules で必要なカラム追加

alter table daily_candidates
  add column if not exists is_active boolean;

alter table daily_candidates
  add column if not exists is_sold boolean default false;

alter table daily_candidates
  add column if not exists lot_size integer;

-- bidding_records テーブル: migration 002 で不足している場合の補完
-- (002 で作成済みなら何も起きない)
create table if not exists bidding_records (
  id               uuid    default gen_random_uuid() primary key,
  candidate_id     text    not null,
  approved_by      text    not null default 'ceo',
  bid_max_jpy      numeric(18,2) not null,
  bid_currency     text    not null default 'USD',
  bid_amount_source numeric(18,2),
  bid_status       text    not null default 'queued'
                   check (bid_status in ('queued','submitted','won','lost','cancelled','failed')),
  external_ref     text,
  note             text,
  submitted_at     timestamptz,
  resolved_at      timestamptz,
  created_at       timestamptz default now(),
  updated_at       timestamptz default now()
);

create index if not exists idx_bidding_records_candidate_id
  on bidding_records(candidate_id);

create index if not exists idx_bidding_records_bid_status
  on bidding_records(bid_status);

-- candidate_status_checks: 追加カラム補完
alter table candidate_status_checks
  add column if not exists is_sold boolean;

alter table candidate_status_checks
  add column if not exists lot_size integer;

alter table candidate_status_checks
  add column if not exists raw_snapshot_json jsonb not null default '{}'::jsonb;

-- candidate_pricing_snapshots: 追加カラム補完
alter table candidate_pricing_snapshots
  add column if not exists recent_3m_avg_jpy     numeric(18,2);

alter table candidate_pricing_snapshots
  add column if not exists recent_3_6m_avg_jpy   numeric(18,2);

alter table candidate_pricing_snapshots
  add column if not exists recent_6_12m_avg_jpy  numeric(18,2);

alter table candidate_pricing_snapshots
  add column if not exists older_12m_plus_avg_jpy numeric(18,2);

alter table candidate_pricing_snapshots
  add column if not exists cost_formula_json jsonb not null default '{}'::jsonb;

alter table candidate_pricing_snapshots
  add column if not exists projected_margin numeric(18,4);
