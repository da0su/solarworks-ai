-- 004_day1_views_and_triggers.sql
-- 最新判断ビュー + daily_candidates 自動同期トリガー

create or replace view latest_candidate_decisions as
select distinct on (candidate_id)
  id,
  candidate_id,
  decision,
  reason_code,
  decision_note,
  decided_by,
  source_screen,
  metadata,
  decided_at,
  created_at
from candidate_decisions
order by candidate_id, decided_at desc, created_at desc;


create or replace function sync_daily_candidates_from_decisions()
returns trigger
language plpgsql
as $$
begin
  update daily_candidates
     set ceo_decision            = new.decision,
         decision_status         = new.decision,
         decision_reason_code    = new.reason_code,
         ceo_decision_note       = new.decision_note,
         decision_last_updated_at = new.decided_at
   where cast(id as text) = new.candidate_id;

  return new;
end;
$$;


drop trigger if exists trg_sync_daily_candidates_from_decisions on candidate_decisions;

create trigger trg_sync_daily_candidates_from_decisions
after insert on candidate_decisions
for each row
execute function sync_daily_candidates_from_decisions();


create or replace function refresh_daily_candidates_evidence_count()
returns void
language sql
as $$
  update daily_candidates dc
     set evidence_count = sub.cnt
    from (
      select candidate_id, count(*)::int as cnt
      from candidate_evidence
      group by candidate_id
    ) sub
   where cast(dc.id as text) = sub.candidate_id;
$$;
