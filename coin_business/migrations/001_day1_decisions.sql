-- 001_day1_decisions.sql
-- CEO判断履歴テーブル（正本）

create extension if not exists pgcrypto;

create table if not exists candidate_decisions (
  id uuid primary key default gen_random_uuid(),
  candidate_id text not null,
  decision text not null check (decision in ('approved', 'rejected', 'held', 'pending', 'auto_rejected', 'auto_review')),
  reason_code text,
  decision_note text,
  decided_by text not null default 'ceo',
  source_screen text not null default 'dashboard',
  metadata jsonb not null default '{}'::jsonb,
  decided_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

create index if not exists idx_candidate_decisions_candidate_id
  on candidate_decisions(candidate_id);

create index if not exists idx_candidate_decisions_decided_at
  on candidate_decisions(decided_at desc);

create index if not exists idx_candidate_decisions_candidate_id_decided_at
  on candidate_decisions(candidate_id, decided_at desc);
