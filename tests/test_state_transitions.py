"""
state遷移テスト — E2E順正系 + 障害系
実行: python -m pytest tests/test_state_transitions.py -v
依存: pytest
"""
import json
import sys
import uuid
import tempfile
import shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta

import pytest

# ルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from slack_bridge import (
    StateManager, ErrorType, TaskStatus,
    TASK_FLOW, _calc_next_action, _classify_error,
    ACK_TIMEOUT_SEC, DONE_TIMEOUT_SEC,
    SOURCE_AUTO, SOURCE_MANUAL,
)


# ============================================================
# フィクスチャ: 一時ディレクトリにStateManagerを作成
# ============================================================
@pytest.fixture
def tmp_state(tmp_path):
    """テスト用の一時 StateManager インスタンス"""
    state_file = tmp_path / "system_state.json"
    mgr = StateManager(path=state_file)
    return mgr


def make_id():
    return str(uuid.uuid4())


# ============================================================
# ヘルパー
# ============================================================
def assert_status(mgr: StateManager, task_id: str, expected: str):
    t = mgr.get_task_by_id(task_id)
    assert t is not None, f"task {task_id[:8]} not found in state"
    assert t["status"] == expected, (
        f"task {t.get('task_name')} expected status={expected}, got={t['status']}"
    )


def assert_in_current(mgr: StateManager, task_id: str):
    state = mgr.load()
    ids = [t["task_id"] for t in state["current_tasks"]]
    assert task_id in ids, f"task {task_id[:8]} not in current_tasks"


def assert_in_history(mgr: StateManager, task_id: str):
    state = mgr.load()
    ids = [t["task_id"] for t in state["recent_history"]]
    assert task_id in ids, f"task {task_id[:8]} not in recent_history"


# ============================================================
# E2E 順正系: git-pull → ebay-search → ebay-review → ceo-report
# ============================================================
class TestE2ESuccessFlow:

    def test_full_chain_state_transitions(self, tmp_state):
        """4タスクが順に queued→running→done と遷移し、
        done 時に next_action が次タスク名を指すことを確認"""
        mgr = tmp_state
        chain = ["git-pull", "ebay-search", "ebay-review", "ceo-report"]

        for i, task_name in enumerate(chain):
            tid = make_id()
            owner = "cyber"
            to = TASK_FLOW[task_name]["to"]

            # 送信側: queued
            mgr.task_queued(tid, task_name, owner, to)
            assert_status(mgr, tid, TaskStatus.QUEUED)
            t = mgr.get_task_by_id(tid)
            assert t["waiting_for"] == to
            assert t["retry_count"] == 0

            # ACK受信: acknowledged
            mgr.task_acknowledged(tid)
            assert_status(mgr, tid, TaskStatus.ACKNOWLEDGED)

            # 実行開始: running
            mgr.task_running(tid)
            assert_status(mgr, tid, TaskStatus.RUNNING)

            # 完了: done → history へ
            mgr.task_done(tid, f"{task_name} completed")
            assert_in_history(mgr, tid)
            t_hist = mgr.get_task_by_id(tid)
            assert t_hist["status"] == TaskStatus.DONE

            # next_action が次タスクを指しているか
            next_task = TASK_FLOW[task_name].get("next")
            if next_task:
                assert next_task in (t_hist.get("next_action") or ""), (
                    f"{task_name} done: next_action should mention {next_task}, "
                    f"got: {t_hist.get('next_action')}"
                )

    def test_dependency_check_passes_after_predecessor_done(self, tmp_state):
        """ebay-search は git-pull が done なら実行可能"""
        mgr = tmp_state
        git_id = make_id()

        # git-pull は depends_on なし → 常に実行可能
        can, reason = mgr.check_dependency("git-pull")
        assert can is True

        # git-pull が未完了の間は ebay-search はブロック
        mgr.task_queued(git_id, "git-pull", "cyber", "cyber")
        mgr.task_running(git_id)
        can, reason = mgr.check_dependency("ebay-search")
        assert can is False
        assert "git-pull" in reason

        # git-pull が done になったら実行可能
        mgr.task_done(git_id)
        can, reason = mgr.check_dependency("ebay-search")
        assert can is True, f"should be runnable after git-pull done, got: {reason}"

    def test_enqueue_next_adds_to_current_tasks(self, tmp_state):
        """enqueue_next が current_tasks に次タスクを追加する"""
        mgr = tmp_state
        git_id = make_id()
        mgr.task_queued(git_id, "git-pull", "cyber", "cyber")
        mgr.task_running(git_id)
        mgr.task_done(git_id)

        next_id = mgr.enqueue_next("git-pull", git_id)
        assert next_id is not None

        state = mgr.load()
        names = [t["task_name"] for t in state["current_tasks"]]
        assert "ebay-search" in names

    def test_enqueue_next_skips_duplicate(self, tmp_state):
        """同名タスクが current_tasks にあれば再 enqueue しない"""
        mgr = tmp_state
        git_id = make_id()
        mgr.task_queued(git_id, "git-pull", "cyber", "cyber")
        mgr.task_running(git_id)
        mgr.task_done(git_id)

        id1 = mgr.enqueue_next("git-pull", git_id)
        id2 = mgr.enqueue_next("git-pull", git_id)   # 2回目はスキップ
        assert id1 is not None
        assert id2 is None

    def test_system_status_transitions(self, tmp_state):
        """system_status が idle → busy → idle と遷移する"""
        mgr = tmp_state
        state = mgr.load()
        assert state["system_status"] == "idle"

        tid = make_id()
        mgr.task_queued(tid, "test-ping", "cyber", "cyber")
        assert mgr.load()["system_status"] == "busy"

        mgr.task_running(tid)
        mgr.task_done(tid)
        assert mgr.load()["system_status"] == "idle"

    def test_recent_history_max_cap(self, tmp_state):
        """recent_history が MAX_HISTORY(50) を超えないこと"""
        from slack_bridge import MAX_HISTORY
        mgr = tmp_state
        for _ in range(MAX_HISTORY + 5):
            tid = make_id()
            mgr.task_queued(tid, "test-ping", "cyber", "cyber")
            mgr.task_running(tid)
            mgr.task_done(tid)
        state = mgr.load()
        assert len(state["recent_history"]) <= MAX_HISTORY


# ============================================================
# 障害系テスト
# ============================================================
class TestFailureScenarios:

    def test_ack_timeout_error_type(self, tmp_state):
        """ACKタイムアウト → error_type=ACK_TIMEOUT"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_queued(tid, "ebay-search", "cyber", "cyber")
        mgr.task_error(tid, "ACK timeout after 3 attempts",
                       error_type=ErrorType.ACK_TIMEOUT)

        t = mgr.get_task_by_id(tid)
        assert t["status"] == TaskStatus.ERROR
        assert t["error_type"] == ErrorType.ACK_TIMEOUT
        assert "retry" in (t.get("next_action") or "").lower()

    def test_done_timeout_error_type(self, tmp_state):
        """DONEタイムアウト → error_type=DONE_TIMEOUT"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_queued(tid, "ebay-search", "cyber", "cyber")
        mgr.task_acknowledged(tid)
        mgr.task_running(tid)
        mgr.task_error(tid, "DONE timeout after 30min",
                       error_type=ErrorType.DONE_TIMEOUT)

        t = mgr.get_task_by_id(tid)
        assert t["error_type"] == ErrorType.DONE_TIMEOUT
        assert "retry" in (t.get("next_action") or "").lower()

    def test_config_missing_error_type(self, tmp_state):
        """Script not found → error_type=CONFIG_MISSING（自動分類）"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_received(tid, "ebay-search", "cyber")
        mgr.task_acknowledged(tid)
        mgr.task_running(tid)
        mgr.task_error(tid, "Script not found: ebay_auction_search.py")

        t = mgr.get_task_by_id(tid)
        assert t["error_type"] == ErrorType.CONFIG_MISSING
        assert "fix config" in (t.get("next_action") or "")

    def test_manual_required_error_type(self, tmp_state):
        """interrupted → error_type=MANUAL_REQUIRED（自動分類）"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_received(tid, "ebay-search", "cyber")
        mgr.task_running(tid)
        mgr.task_error(tid, "interrupted: watch restarted")

        t = mgr.get_task_by_id(tid)
        assert t["error_type"] == ErrorType.MANUAL_REQUIRED
        assert "manual" in (t.get("next_action") or "").lower()

    def test_dependency_blocked(self, tmp_state):
        """依存タスクが失敗 → 後続は blocked、error_type=DEPENDENCY_FAILED"""
        mgr = tmp_state
        git_id = make_id()
        ebay_id = make_id()

        # git-pull を error で終わらせる
        mgr.task_queued(git_id, "git-pull", "cyber", "cyber")
        mgr.task_running(git_id)
        mgr.task_error(git_id, "connection refused")

        # ebay-search の依存チェック → blocked のはず
        can, reason = mgr.check_dependency("ebay-search")
        assert can is False
        assert "git-pull" in reason

        # ebay-search を blocked 状態にする
        mgr.task_queued(ebay_id, "ebay-search", "cyber", "cyber")
        mgr.task_blocked(ebay_id, reason or "git-pull failed")

        t = mgr.get_task_by_id(ebay_id)
        assert t["status"] == TaskStatus.BLOCKED
        assert t["error_type"] == ErrorType.DEPENDENCY_FAILED

    def test_dependency_not_yet_run(self, tmp_state):
        """前提タスクが history にない → 実行不可"""
        mgr = tmp_state
        can, reason = mgr.check_dependency("ebay-search")
        assert can is False
        assert "not run yet" in reason or "in progress" in reason or "git-pull" in reason

    def test_waiting_manual_status(self, tmp_state):
        """waiting_manual ステータスと system_status の確認"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_received(tid, "ebay-review", "cap")
        mgr.task_running(tid)
        mgr.task_waiting_manual(tid, "CEO approval required")

        t = mgr.get_task_by_id(tid)
        assert t["status"] == TaskStatus.WAITING_MANUAL
        assert t["error_type"] == ErrorType.MANUAL_REQUIRED

        state = mgr.load()
        assert state["system_status"] == "waiting_manual"

    def test_retry_increments_count(self, tmp_state):
        """task_retry がリトライカウントを増やし status=queued に戻す"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_queued(tid, "test-ping", "cyber", "cyber")
        mgr.task_retry(tid)

        t = mgr.get_task_by_id(tid)
        assert t["retry_count"] == 1
        assert t["status"] == TaskStatus.QUEUED
        assert t["last_error"] is None

    def test_atomic_write_no_corruption(self, tmp_state):
        """atomic write で state ファイルが壊れないことを確認"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_queued(tid, "test-ping", "cyber", "cyber")

        # ファイルが有効な JSON であること
        raw = mgr.path.read_text(encoding="utf-8")
        parsed = json.loads(raw)   # 例外が出なければ OK
        assert "current_tasks" in parsed
        assert "recent_history" in parsed

    def test_load_recovers_from_corrupt_file(self, tmp_state):
        """壊れた JSON でも load() がデフォルト状態を返す"""
        mgr = tmp_state
        mgr.path.write_text("{ broken json !!!", encoding="utf-8")
        state = mgr.load()
        assert state["current_tasks"] == []
        assert state["recent_history"] == []


# ============================================================
# error 分類ユニットテスト
# ============================================================
class TestErrorClassification:

    @pytest.mark.parametrize("msg,expected", [
        ("ACK timed out after 3 attempts",   ErrorType.ACK_TIMEOUT),
        ("DONE timeout after 30min",         ErrorType.DONE_TIMEOUT),
        ("Script not found: foo.py",         ErrorType.CONFIG_MISSING),
        ("file not found",                   ErrorType.CONFIG_MISSING),
        ("interrupted: watch restarted",     ErrorType.MANUAL_REQUIRED),
        ("ZeroDivisionError: division by 0", ErrorType.EXECUTION_ERROR),
        ("some unknown thing happened",      ErrorType.EXECUTION_ERROR),
    ])
    def test_classify_error(self, msg, expected):
        assert _classify_error(msg) == expected


# ============================================================
# state 監査テスト
# ============================================================
class TestStateAudit:

    def test_audit_detects_timeout_exceeded(self, tmp_state):
        """timeout_at を過ぎた running タスクを監査が検出する"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_queued(tid, "test-ping", "cyber", "cyber")
        mgr.task_acknowledged(tid)
        mgr.task_running(tid)

        # timeout_at を過去に書き換える
        state = mgr.load()
        for t in state["current_tasks"]:
            if t["task_id"] == tid:
                past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                t["timeout_at"] = past
        mgr.save(state)

        issues = mgr.audit()
        assert any(i["task_id"] == tid[:8] for i in issues), \
            f"Audit should detect timeout for {tid[:8]}, got: {issues}"

    def test_audit_clean_state_no_issues(self, tmp_state):
        """正常な idle 状態では問題なし"""
        mgr = tmp_state
        issues = mgr.audit()
        assert issues == []


# ============================================================
# blocked/error 分離テスト
# ============================================================
class TestBlockedErrorSeparation:

    def test_blocked_not_counted_as_error(self, tmp_state):
        """blocked タスクは error 件数に含まれない"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_queued(tid, "ebay-search", "cyber", "cyber")
        mgr.task_blocked(tid, "git-pull has not run yet")

        t = mgr.get_task_by_id(tid)
        assert t["status"] == TaskStatus.BLOCKED
        assert t["error_type"] == ErrorType.DEPENDENCY_FAILED

        state = mgr.load()
        error_tasks = [x for x in state["current_tasks"]
                       if x.get("status") == TaskStatus.ERROR]
        blocked_tasks = [x for x in state["current_tasks"]
                         if x.get("status") == TaskStatus.BLOCKED]
        assert len(error_tasks) == 0
        assert len(blocked_tasks) == 1

    def test_system_status_blocked_vs_error(self, tmp_state):
        """blocked 時は system_status=blocked、error 時は error"""
        mgr = tmp_state
        tid_b = make_id()
        mgr.task_queued(tid_b, "ebay-search", "cyber", "cyber")
        mgr.task_blocked(tid_b, "dependency not met")
        assert mgr.load()["system_status"] == "blocked"

        tid_e = make_id()
        mgr.task_queued(tid_e, "test-ping", "cyber", "cyber")
        mgr.task_running(tid_e)
        mgr.task_error(tid_e, "connection refused")
        assert mgr.load()["system_status"] == "error"


# ============================================================
# 自動enqueue 安全化テスト
# ============================================================
class TestEnqueueSafety:

    def test_source_auto_on_enqueue_next(self, tmp_state):
        """enqueue_next で作られたタスクの source=auto"""
        mgr = tmp_state
        git_id = make_id()
        mgr.task_queued(git_id, "git-pull", "cyber", "cyber")
        mgr.task_running(git_id)
        mgr.task_done(git_id)

        next_id = mgr.enqueue_next("git-pull", git_id)
        assert next_id is not None
        t = mgr.get_task_by_id(next_id)
        assert t["source"] == SOURCE_AUTO

    def test_source_manual_on_task_queued(self, tmp_state):
        """task_queued（手動起動）の source=manual"""
        mgr = tmp_state
        tid = make_id()
        mgr.task_queued(tid, "test-ping", "cyber", "cyber")
        t = mgr.get_task_by_id(tid)
        assert t["source"] == SOURCE_MANUAL

    def test_enqueue_next_skips_running_task(self, tmp_state):
        """next タスクが running 中なら enqueue しない"""
        mgr = tmp_state
        git_id   = make_id()
        ebay_id  = make_id()

        mgr.task_queued(git_id, "git-pull", "cyber", "cyber")
        mgr.task_running(git_id)
        mgr.task_done(git_id)

        # ebay-search を手動で running 状態にしておく
        mgr.task_queued(ebay_id, "ebay-search", "cyber", "cyber")
        mgr.task_running(ebay_id)

        result = mgr.enqueue_next("git-pull", git_id)
        assert result is None, "should not enqueue when ebay-search already running"

    def test_enqueue_next_skips_recent_done(self, tmp_state):
        """直近60秒以内に完了した同名タスクがあれば enqueue しない"""
        from datetime import datetime, timezone
        mgr = tmp_state
        git_id = make_id()
        mgr.task_queued(git_id, "git-pull", "cyber", "cyber")
        mgr.task_running(git_id)
        mgr.task_done(git_id)

        # ebay-search を完了させて history に入れる
        ebay_id = make_id()
        mgr.task_queued(ebay_id, "ebay-search", "cyber", "cyber")
        mgr.task_running(ebay_id)
        mgr.task_done(ebay_id)

        # git-pull の enqueue_next（ebay-search）→ 直近 done があるのでスキップ
        result = mgr.enqueue_next("git-pull", git_id)
        assert result is None, "should not re-enqueue ebay-search completed <60s ago"


# ============================================================
# ceo-report 承認フロー テスト
# ============================================================
class TestCeoReportApproval:

    def _setup_ebay_review_done(self, mgr):
        """ebay-review を done 状態にする共通セットアップ"""
        # 依存チェーンを通す（git-pull → ebay-search → ebay-review）
        for task in ["git-pull", "ebay-search", "ebay-review"]:
            tid = make_id()
            mgr.task_queued(tid, task, "cyber", "cyber")
            mgr.task_running(tid)
            mgr.task_done(tid)

    def test_ceo_report_blocked_without_approval(self, tmp_state):
        """ebay-review が done でも承認なしなら ceo-report はブロック"""
        mgr = tmp_state
        self._setup_ebay_review_done(mgr)

        can, reason = mgr.check_dependency("ceo-report")
        assert can is False
        assert "not approved" in reason or "review_status" in reason

    def test_ceo_report_allowed_after_approval(self, tmp_state):
        """cap 承認後は ceo-report の依存チェックが通る"""
        mgr = tmp_state
        self._setup_ebay_review_done(mgr)

        ok = mgr.approve_task("ebay-review", "cap")
        assert ok is True

        can, reason = mgr.check_dependency("ceo-report")
        assert can is True, f"ceo-report should run after approval, got: {reason}"

    def test_approve_sets_fields(self, tmp_state):
        """approve_task が approved_by / approved_at / review_status を記録する"""
        mgr = tmp_state
        self._setup_ebay_review_done(mgr)
        mgr.approve_task("ebay-review", "cap")

        state = mgr.load()
        approved = None
        for t in state["recent_history"]:
            if t.get("task_name") == "ebay-review" and t.get("status") == TaskStatus.DONE:
                approved = t
                break
        assert approved is not None
        assert approved["review_status"] == "approved"
        assert approved["approved_by"] == "cap"
        assert approved["approved_at"] is not None

    def test_approve_fails_without_done_task(self, tmp_state):
        """done エントリがない状態で approve → False"""
        mgr = tmp_state
        ok = mgr.approve_task("ebay-review", "cap")
        assert ok is False

    def test_is_approved_false_before_approval(self, tmp_state):
        """承認前は is_approved=False"""
        mgr = tmp_state
        self._setup_ebay_review_done(mgr)
        assert mgr.is_approved("ebay-review") is False

    def test_is_approved_true_after_approval(self, tmp_state):
        """承認後は is_approved=True"""
        mgr = tmp_state
        self._setup_ebay_review_done(mgr)
        mgr.approve_task("ebay-review", "cap")
        assert mgr.is_approved("ebay-review") is True
