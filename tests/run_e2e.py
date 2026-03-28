"""
E2E 実機テストスクリプト — git-pull -> ebay-search -> ebay-review -> approve -> ceo-report

実行方法:
  python tests/run_e2e.py

前提:
  - Slack接続不要（StateManager の state ファイルを直接操作）
  - 一時ディレクトリを使用するためプロダクション state に影響なし
  - 各ステップで state-summary 相当の出力を表示
  - 最後に state-audit が CLEAN であることを確認

シミュレーション方針:
  - 単一 StateManager で送受信両側の遷移を統合的にテスト
  - 各タスクは task_acknowledged -> task_running -> task_done のパスを通る
    (task_queued は enqueue_next / 手動キュー投入で作成)
  - これにより分散環境の「受信側が state を更新する」流れを再現

テストシナリオ:
  Step 1: git-pull を手動キュー (task_queued) -> acknowledged -> running -> done
  Step 2: enqueue_next で ebay-search auto-enqueue -> acknowledged -> running -> done
  Step 3: enqueue_next で ebay-review auto-enqueue -> acknowledged -> running -> done
  Step 4: ceo-report dependency check -> blocked（承認なし）
  Step 5: approve ebay-review -> ceo-report auto-enqueue
  Step 6: ceo-report acknowledged -> running -> done -> report_status=sent
  Final:  workflow_id 一貫性確認 + state-audit CLEAN
"""

import sys
import uuid
import tempfile
from pathlib import Path

# ルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from slack_bridge import (
    StateManager, TaskStatus,
    TASK_FLOW, SOURCE_AUTO, SOURCE_MANUAL,
    CEO_ROOM_CHANNEL,
)


# ============================================================
# ユーティリティ
# ============================================================

W = 60


def section(title: str):
    print(f"\n{'='*W}")
    print(f"  {title}")
    print(f"{'='*W}")


def print_state_summary(mgr: StateManager, label: str = ""):
    state = mgr.load()
    print(f"\n--- State Summary{' [' + label + ']' if label else ''} ---")
    print(f"  system_status : {state['system_status']}")
    print(f"  next_action   : {state.get('next_action') or '-'}")

    current = state.get("current_tasks", [])
    if current:
        print(f"  current_tasks ({len(current)}):")
        for t in current:
            wf = (t.get("workflow_id") or "")[:8]
            print(f"    [{t['status']:<14}] {t['task_name']:<14} wf={wf} src={t.get('source','?')}")
    else:
        print(f"  current_tasks : (none)")

    history = state.get("recent_history", [])
    if history:
        print(f"  recent_history ({len(history)}):")
        for t in history[:6]:
            wf = (t.get("workflow_id") or "")[:8]
            rv = t.get("review_status") or ""
            rs = t.get("report_status") or ""
            extra = ""
            if rv:
                extra += f" review={rv}"
            if rs:
                extra += f" report={rs} ch={t.get('reported_channel','?')}"
            print(f"    [{t['status']:<14}] {t['task_name']:<14} wf={wf}{extra}")


def check(condition: bool, msg: str):
    if not condition:
        raise AssertionError(msg)
    print(f"  [OK] {msg}")


def assert_task_status(mgr: StateManager, task_id: str, expected: str, label: str = ""):
    t = mgr.get_task_by_id(task_id)
    check(t is not None, f"{label or task_id[:8]}: task found")
    check(t["status"] == expected,
          f"{label or t.get('task_name')}: status={expected} (got {t['status']})")


def assert_workflow_chain(mgr: StateManager, task_ids: list, expected_wf: str):
    for tid in task_ids:
        t = mgr.get_task_by_id(tid)
        if t is None:
            raise AssertionError(f"task {tid[:8]} not found for workflow check")
        actual = t.get("workflow_id", "")
        check(actual == expected_wf,
              f"{t.get('task_name')} workflow_id={actual[:8]} matches chain")


def run_task_lifecycle(mgr: StateManager, task_id: str, task_name: str,
                       result_summary: str = None):
    """QUEUED エントリを acknowledged -> running -> done に遷移させる"""
    mgr.task_acknowledged(task_id)
    mgr.task_running(task_id)
    mgr.task_done(task_id, result_summary or f"{task_name} completed")


def run_audit_check(mgr: StateManager):
    section("Final: state-audit")
    issues = mgr.audit()
    if not issues:
        print("  RESULT: CLEAN (no issues)")
    else:
        for iss in issues:
            print(f"  [{iss['level']}] {iss.get('task_name','?')}: {iss['issue']}")
    check(not issues, f"state-audit CLEAN ({len(issues)} issue(s) found)")


# ============================================================
# E2E メインシナリオ
# ============================================================

def run_e2e():
    with tempfile.TemporaryDirectory() as tmp_dir:
        state_file = Path(tmp_dir) / "system_state.json"
        mgr = StateManager(path=state_file)

        section("Setup")
        print("  Chain: git-pull -> ebay-search -> ebay-review -> (approve) -> ceo-report")
        print("  Note: single StateManager simulates both sender and receiver")

        # 全チェーンの workflow_id（手動起動時に生成）
        wf_id = str(uuid.uuid4())
        print(f"  workflow_id: {wf_id[:8]}")

        # ---------------------------------------------------------
        # Step 1: git-pull をキュー投入 -> 完了
        # ---------------------------------------------------------
        section("Step 1: git-pull queued -> done")

        pull_id = str(uuid.uuid4())
        mgr.task_queued(pull_id, "git-pull", "cap", "cyber",
                        workflow_id=wf_id)
        assert_task_status(mgr, pull_id, TaskStatus.QUEUED, "git-pull")

        run_task_lifecycle(mgr, pull_id, "git-pull", "git pull origin main: up to date")
        assert_task_status(mgr, pull_id, TaskStatus.DONE, "git-pull")

        state = mgr.load()
        check(state["system_status"] == "idle", "system_status=idle after git-pull done")
        print_state_summary(mgr, "after git-pull done")

        # ---------------------------------------------------------
        # Step 2: ebay-search auto-enqueue -> 完了
        # ---------------------------------------------------------
        section("Step 2: ebay-search auto-enqueue -> done")

        search_id = mgr.enqueue_next("git-pull", pull_id)
        check(search_id is not None, "ebay-search auto-enqueued after git-pull done")
        assert_task_status(mgr, search_id, TaskStatus.QUEUED, "ebay-search")

        # source=auto 確認
        t = mgr.get_task_by_id(search_id)
        check(t.get("source") == SOURCE_AUTO, "ebay-search source=auto")

        run_task_lifecycle(mgr, search_id, "ebay-search", "found 12 candidates")
        assert_task_status(mgr, search_id, TaskStatus.DONE, "ebay-search")
        print_state_summary(mgr, "after ebay-search done")

        # ---------------------------------------------------------
        # Step 3: ebay-review auto-enqueue -> 完了
        # ---------------------------------------------------------
        section("Step 3: ebay-review auto-enqueue -> done")

        review_id = mgr.enqueue_next("ebay-search", search_id)
        check(review_id is not None, "ebay-review auto-enqueued after ebay-search done")
        assert_task_status(mgr, review_id, TaskStatus.QUEUED, "ebay-review")

        run_task_lifecycle(mgr, review_id, "ebay-review", "3 candidates approved")
        assert_task_status(mgr, review_id, TaskStatus.DONE, "ebay-review")
        print_state_summary(mgr, "after ebay-review done")

        # ---------------------------------------------------------
        # Step 4: ceo-report dependency check -> blocked（承認なし）
        # ---------------------------------------------------------
        section("Step 4: ceo-report blocked before approval")

        can_run, reason = mgr.check_dependency("ceo-report")
        check(not can_run, "ceo-report dependency check: can_run=False before approve")
        check(
            "approved" in (reason or "").lower() or "pending" in (reason or "").lower(),
            f"block reason mentions approval: '{reason}'"
        )
        print(f"  [OK] block reason: {reason}")

        # ebay-review の直後に enqueue_next を呼ぶと ceo-report はブロックされず skip になる
        # (check_dependency は enqueue_next の前段ではなく dispatch_task で呼ぶ設計)
        # ここでは enqueue_next が None を返すことを確認（承認済みではないため条件分岐でスキップ）
        # ※実際の skip ロジックは enqueue_next 内の guard ではなく check_dependency
        # → enqueue_next 自体は next タスクを無条件に enqueue する設計。
        #   dispatch_task 側で check_dependency を呼ぶ。
        # → ここでは check_dependency の結果を確認するだけで十分。
        print_state_summary(mgr, "before approval")

        # ---------------------------------------------------------
        # Step 5: approve ebay-review -> ceo-report auto-enqueue
        # ---------------------------------------------------------
        section("Step 5: approve ebay-review -> ceo-report auto-enqueue")

        ok = mgr.approve_task("ebay-review", "cap")
        check(ok, "approve_task returned True")

        # ebay-review history entry に review_status が設定されていること
        state = mgr.load()
        review_entry = next(
            (t for t in state.get("recent_history", [])
             if t.get("task_name") == "ebay-review" and t.get("status") == TaskStatus.DONE),
            None
        )
        check(review_entry is not None, "ebay-review done entry in history")
        check(review_entry.get("review_status") == "approved", "review_status=approved")
        check(review_entry.get("approved_by") == "cap", "approved_by=cap")
        check(review_entry.get("approved_at") is not None, "approved_at set")

        # 承認後 check_dependency が True になる
        can_run, reason = mgr.check_dependency("ceo-report")
        check(can_run, f"ceo-report can_run=True after approve (reason={reason})")

        # auto enqueue
        report_id = mgr.enqueue_next("ebay-review", "approved")
        check(report_id is not None, "ceo-report auto-enqueued after approve")
        assert_task_status(mgr, report_id, TaskStatus.QUEUED, "ceo-report")

        t = mgr.get_task_by_id(report_id)
        check(t.get("source") == SOURCE_AUTO, "ceo-report source=auto")

        print_state_summary(mgr, "after approve + ceo-report enqueued")

        # ---------------------------------------------------------
        # Step 6: ceo-report 完了 -> report_status=sent
        # ---------------------------------------------------------
        section("Step 6: ceo-report done -> report_status=sent")

        run_task_lifecycle(mgr, report_id, "ceo-report", "CEO report sent")
        assert_task_status(mgr, report_id, TaskStatus.DONE, "ceo-report")

        # report_status を記録
        ok = mgr.task_report_sent(report_id, CEO_ROOM_CHANNEL, status="sent")
        check(ok, "task_report_sent returned True")

        t = mgr.get_task_by_id(report_id)
        check(t is not None, "ceo-report entry found after done")
        check(t.get("report_status") == "sent", f"report_status=sent (got {t.get('report_status')})")
        check(t.get("reported_channel") == CEO_ROOM_CHANNEL,
              f"reported_channel={CEO_ROOM_CHANNEL}")
        check(t.get("reported_at") is not None, "reported_at set")
        print(f"  [OK] reported_at={t['reported_at'][:19]}")

        print_state_summary(mgr, "after ceo-report done")

        # ---------------------------------------------------------
        # Step 7: workflow_id 一貫性確認
        # ---------------------------------------------------------
        section("Step 7: workflow_id chain consistency")

        # git-pull は手動で wf_id を指定 -> それ以降は enqueue_next が引き継ぐ
        assert_workflow_chain(mgr, [pull_id, search_id, review_id, report_id], wf_id)

        # ---------------------------------------------------------
        # Step 8: 重複 enqueue ガード
        # ---------------------------------------------------------
        section("Step 8: duplicate enqueue guard")

        # ceo-report は直近60秒以内に done -> 再度 enqueue_next を呼んでも skip
        dup_id = mgr.enqueue_next("ebay-review", "approved")
        check(dup_id is None, "duplicate enqueue blocked by guard (recent done <60s)")

        # ---------------------------------------------------------
        # Final: state-audit CLEAN
        # ---------------------------------------------------------
        run_audit_check(mgr)

        # ---------------------------------------------------------
        # 最終サマリー
        # ---------------------------------------------------------
        section("RESULT")
        state = mgr.load()
        done_count = sum(
            1 for t in state.get("recent_history", [])
            if t.get("status") == TaskStatus.DONE
        )
        print(f"  All steps passed.")
        print(f"  Tasks completed  : {done_count}")
        print(f"  system_status    : {state['system_status']}")
        print(f"  workflow_id chain: {wf_id[:8]}")
        print(f"\n  E2E TEST PASSED")


if __name__ == "__main__":
    try:
        run_e2e()
    except AssertionError as e:
        print(f"\n  E2E TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        import traceback
        print(f"\n  E2E TEST ERROR: {e}")
        traceback.print_exc()
        sys.exit(2)
