@echo off
REM ============================================================
REM DISABLED 2026-08-01 by CEO decision.
REM   "If it is not being used, stop it."
REM
REM Why (measured):
REM   1. This bat had no output redirection, so the python print()
REM      was discarded by Task Scheduler background execution.
REM      = A report was built 3x/day and thrown away unseen.
REM   2. Slack sending already off (SLACK_PERIODIC_REPORT_ENABLED=False,
REM      plus SLACK_FULL_STOP since 2026-07-23).
REM   3. Its content (target / progress / rate / cumulative / last run /
REM      stall + rate_limit detection) is fully covered by:
REM        - patrol_v6              : same checks every 15min + auto recovery
REM        - room_status.py --human : SSOT truth for all 4 functions
REM        - daily_direction.py     : daily scorecard (22:00)
REM      Running it 3x/day was pure duplication.
REM
REM The tasks FollowReport_AM/PM/Night cannot be disabled without admin
REM rights, so we exit immediately here to neutralize them.
REM
REM To re-enable : delete the "exit /b 0" line below.
REM To delete for good, run in an ADMIN PowerShell:
REM   Unregister-ScheduledTask -TaskName FollowReport_AM,FollowReport_PM,FollowReport_Night -Confirm:$false
REM ============================================================
exit /b 0

cd /d C:\Users\infoa\Documents\solarworks-ai
set PYTHONIOENCODING=utf-8
python ops\follow_status_report.py
