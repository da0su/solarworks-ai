#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-05-05 Phase C-1: fail_reason taxonomy（5グループ統一）

楽天ROOM フォロー機能の 20+ 種類の fail_reason を 5 つの意味グループに分類し、
グループ単位で自動復旧マトリクスを構築する基盤。

【設計思想】
従来は fail_reason ごとに ad-hoc に処理していたが、reason の数が増えるたびに
patrol/orchestrator/executor に分岐が増える nightmare 状態だった。
group 単位で統一することで:
  - patrol: group=auth → CEO通知 / group=env → 解像度再キャリブ / etc
  - 新規 reason 追加時はマップにエントリ1行追加するだけ
  - SLO に group 単位の閾値を定義可能 (auth=0/h, verify<20%, rate<5/h)

【グループ定義】
- auth      : 認証・ログイン関連 (login_expired, page_signature_mismatch with /nid/)
- env       : 環境・実行状態 (foreground_mismatch, bbox_misaligned, row_alignment_mismatch)
- rate      : レート制限 (rate_limit_detected)
- seed      : seed/対象ユーザー側問題 (already_followed, budget_exhausted, modal_open_failed,
              no_button_detected, page_signature_mismatch generic)
- verify    : verify サンプリング起因 (verify_sample_*, button_state_changed_*, ui_delay)
"""
from __future__ import annotations
from typing import Dict, List, Optional


# ==================================================
# 5 グループ定義
# ==================================================

GROUP_AUTH = "auth"
GROUP_ENV = "env"
GROUP_RATE = "rate"
GROUP_SEED = "seed"
GROUP_VERIFY = "verify"
GROUP_UNKNOWN = "unknown"

ALL_GROUPS = [GROUP_AUTH, GROUP_ENV, GROUP_RATE, GROUP_SEED, GROUP_VERIFY]


# ==================================================
# fail_reason -> group マップ
#   注意: 新規 reason 追加時はここに必ず追記すること（patrol_hourly が group で集計）
# ==================================================

REASON_TO_GROUP: Dict[str, str] = {
    # AUTH: ログイン失効・セッション関連
    "login_expired": GROUP_AUTH,
    "page_signature_mismatch_login": GROUP_AUTH,  # /nid/ や /login にリダイレクト

    # ENV: 環境（解像度・前景・座標系）
    "foreground_mismatch": GROUP_ENV,
    "bbox_misaligned": GROUP_ENV,
    "bbox_center_wrong": GROUP_ENV,
    "row_alignment_mismatch": GROUP_ENV,
    "click_intercepted": GROUP_ENV,
    "y_below_content_area": GROUP_ENV,
    "click_x_too_right": GROUP_ENV,
    "input_target_uncertain": GROUP_ENV,

    # RATE: 楽天側のレート制限
    "rate_limit_detected": GROUP_RATE,

    # SEED: 対象ユーザー側の問題（修正不要）
    "already_followed": GROUP_SEED,
    "already_followed_missed": GROUP_SEED,
    "modal_open_failed": GROUP_SEED,        # /followers モーダル開かない（seed が削除された等）
    "no_button_detected": GROUP_SEED,
    "page_signature_mismatch": GROUP_SEED,  # 一般版（/feed が表示されない等）
    "unexpected_navigation": GROUP_SEED,
    "budget_exhausted": GROUP_SEED,         # session 内で seed を使い切った
    "ui_mismatch": GROUP_SEED,

    # VERIFY: verify サンプリング起因（Phase A-3 で大幅改善済）
    "verify_sample_on_white_area": GROUP_VERIFY,
    "verify_sample_out_of_button": GROUP_VERIFY,
    "button_state_changed_but_verify_missed": GROUP_VERIFY,
    "ui_delay_before_verify": GROUP_VERIFY,
    "visible_but_unbound": GROUP_VERIFY,
}


# ==================================================
# group ごとの自動復旧アクション (推奨)
# ==================================================

GROUP_RECOVERY_ACTIONS: Dict[str, str] = {
    GROUP_AUTH:    "CEO_notify+abort_session",       # CEO 手動再ログイン
    GROUP_ENV:     "recalibrate_or_relaunch",        # Chrome 再起動 + 解像度再確認
    GROUP_RATE:    "cooldown_90min",                 # 90分待機 (FOLLOW_RL_COOLDOWN_MIN=69+α)
    GROUP_SEED:    "rotate_seed",                    # 次の seed に切替（自動・不要）
    GROUP_VERIFY:  "no_action_needed",               # 既に retry 機構実装済 (Phase A-3)
}


# ==================================================
# group ごとの SLO (許容範囲)
# ==================================================

GROUP_SLO: Dict[str, Dict] = {
    GROUP_AUTH:    {"max_per_hour": 0,  "alert_level": "CRITICAL"},
    GROUP_ENV:     {"max_per_hour": 5,  "alert_level": "WARN"},
    GROUP_RATE:    {"max_per_hour": 3,  "alert_level": "WARN"},
    GROUP_SEED:    {"max_per_hour": 100,"alert_level": "INFO"},   # 通常運用で多数発生
    GROUP_VERIFY:  {"max_per_hour": 20, "alert_level": "WARN"},   # Phase A-3 後の SLO
}


# ==================================================
# 公開関数
# ==================================================

def classify(reason: str) -> str:
    """fail_reason 文字列を 5 グループに分類する.

    Args:
        reason: fail_reason 文字列 (例: "verify_sample_on_white_area")
    Returns:
        グループ名 (例: "verify"). 未登録 reason は "unknown" を返す.
    """
    return REASON_TO_GROUP.get(reason, GROUP_UNKNOWN)


def aggregate_by_group(fail_counts: Dict[str, int]) -> Dict[str, int]:
    """fail_reason 別カウント dict を group 別カウントに集約する.

    Args:
        fail_counts: {"verify_sample_on_white_area": 50, "modal_open_failed": 12, ...}
    Returns:
        {"verify": 50, "seed": 12, ...} (空 group は省略)
    """
    out: Dict[str, int] = {}
    for reason, count in fail_counts.items():
        if not count:
            continue
        g = classify(reason)
        out[g] = out.get(g, 0) + count
    return out


def get_recovery_action(group: str) -> str:
    """group の推奨復旧アクションを返す."""
    return GROUP_RECOVERY_ACTIONS.get(group, "investigate")


def get_slo(group: str) -> Optional[Dict]:
    """group の SLO 定義を返す."""
    return GROUP_SLO.get(group)


def evaluate_slo(fail_counts: Dict[str, int], duration_hours: float = 1.0) -> List[Dict]:
    """fail_counts を SLO に照らして違反を返す.

    Args:
        fail_counts: fail_reason 別カウント
        duration_hours: 集計対象時間（時間単位）
    Returns:
        違反 list. each: {"group": str, "actual": int, "limit": int, "level": str}
    """
    aggregated = aggregate_by_group(fail_counts)
    violations = []
    for group, count in aggregated.items():
        slo = GROUP_SLO.get(group)
        if not slo:
            continue
        per_hour = count / max(0.1, duration_hours)
        if per_hour > slo["max_per_hour"]:
            violations.append({
                "group": group,
                "actual_per_hour": round(per_hour, 1),
                "limit_per_hour": slo["max_per_hour"],
                "level": slo["alert_level"],
                "recovery_action": GROUP_RECOVERY_ACTIONS.get(group, "investigate"),
            })
    return violations


# ==================================================
# 自己テスト
# ==================================================

if __name__ == "__main__":
    # 動作確認
    print("=== fail_reason taxonomy 動作確認 ===")
    sample = {
        "already_followed": 200,
        "verify_sample_on_white_area": 50,
        "verify_sample_out_of_button": 30,
        "modal_open_failed": 12,
        "rate_limit_detected": 4,
        "foreground_mismatch": 1,
    }
    print("Input:", sample)
    print("By group:", aggregate_by_group(sample))
    print("SLO violations (1h):")
    for v in evaluate_slo(sample, duration_hours=1.0):
        print(f"  - {v}")
