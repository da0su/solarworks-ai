# -*- coding: utf-8 -*-
"""Slack 送信 一括 kill スイッチ (CEO 2026-06-04「Slackへの連絡も止めて」).

state/SLACK_DISABLED が存在する間、全ての Slack 送信関数は送信せず即 return する。
再開: state/SLACK_DISABLED を削除するだけ (コード変更不要).

各送信関数の先頭で:
    from ops.notifications.slack_killswitch import slack_disabled
    if slack_disabled():
        return False   # or return / None など各関数の戻り型に合わせる
を呼ぶ。import 経路が通らない箇所は absolute path 版 _flag_exists() を直接使う。
"""
import os

# サイバーさん(Desktop B) 固定パス. 環境変数 SOLARWORKS_ROOT があれば優先。
_ROOT = os.environ.get("SOLARWORKS_ROOT", r"C:\Users\infoa\Documents\solarworks-ai")
FLAG_PATH = os.path.join(_ROOT, "state", "SLACK_DISABLED")


FULL_STOP_PATH = os.path.join(_ROOT, "state", "SLACK_FULL_STOP")


def slack_full_stop() -> bool:
    """CEO 2026-07-23「スラックでの報告を全て終了させて。今後は、報告必要なし」

    SLACK_DISABLED との違い: critical bypass も含めて **例外なく** 停止する。
    SLACK_DISABLED は critical を貫通させるため、7/23 に patrol の CRITICAL が
    連投された。こちらは貫通口を持たない。
    検知内容は state/slack_suppressed.log に残り、対話セッションで確認する。
    再開: state/SLACK_FULL_STOP を削除する。
    """
    try:
        return os.path.exists(FULL_STOP_PATH)
    except Exception:
        return False


def slack_disabled() -> bool:
    """state/SLACK_DISABLED が存在すれば True (= 全 Slack 送信を停止)."""
    try:
        if os.path.exists(FLAG_PATH):
            return True
        # フォールバック: 環境変数でも止められる
        return os.environ.get("SLACK_DISABLED", "") not in ("", "0", "false", "False")
    except Exception:
        return False
