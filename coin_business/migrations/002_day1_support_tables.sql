-- 002_day1_support_tables.sql
-- 証拠・状態確認・入札・価格スナップショットテーブル

create table if not exists candidate_evidence (
  id uuid primary key default gen_random_uuid(),
  candidate_id text not null,
  evidence_type text not null check (
    evidence_type in (
      'source_listing',
      'cert_verification',
      'yahoo_comp',
      'heritage_comp',
      'spink_comp',
      'numista_ref',
      'image',
      'other'
    )
  ),
  evidence_url text not null,
  title text,
  meta_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_candidate_evidence_candidate_id
  on candidate_evidence(candidate_id);

create index if not exists idx_candidate_evidence_type
  on candidate_evidence(evidence_type);


create table if not exists candidate_status_checks (
  id uuid primary key default gen_random_uuid(),
  candidate_id text not null,
  checked_at timestamptz not null default now(),
  is_active boolean,
  is_sold boolean,
  current_price numeric(18,2),
  source_currency text,
  shipping_from_country text,
  lot_size integer,
  raw_snapshot_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists idx_candidate_status_checks_candidate_id
  on candidate_status_checks(candidate_id);

create index if not exists idx_candidate_status_checks_checked_at
  on candidate_status_checks(checked_at desc);


create table if not exists bidding_records (
  id uuid primary key default gen_random_uuid(),
  candidate_id text not null,
  approved_by text,
  bid_max_jpy numeric(18,2),
  bid_currency text,
  bid_amount_source numeric(18,2),
  bid_status text not null default 'queued' check (
    bid_status in ('queued', 'submitted', 'won', 'lost', 'cancelled', 'failed')
  ),
  submitted_at timestamptz,
  resolved_at timestamptz,
  external_ref text,
  note text,
  created_at timestamptz not null default now()
);

create index if not exists idx_bidding_records_candidate_id
  on bidding_records(candidate_id);

create index if not exists idx_bidding_records_status
  on bidding_records(bid_status);


create table if not exists candidate_pricing_snapshots (
  id uuid primary key default gen_random_uuid(),
  candidate_id text not null,
  expected_sale_price_jpy numeric(18,2),
  recent_3m_avg_jpy numeric(18,2),
  recent_3_6m_avg_jpy numeric(18,2),
  recent_6_12m_avg_jpy numeric(18,2),
  older_12m_plus_avg_jpy numeric(18,2),
  cost_formula_json jsonb not null default '{}'::jsonb,
  total_cost_jpy numeric(18,2),
  projected_profit_jpy numeric(18,2),
  projected_roi numeric(18,4),
  projected_margin numeric(18,4),
  created_at timestamptz not null default now()
);

create index if not exists idx_candidate_pricing_snapshots_candidate_id
  on candidate_pricing_snapshots(candidate_id);

create index if not exists idx_candidate_pricing_snapshots_created_at
  on candidate_pricing_snapshots(created_at desc);
