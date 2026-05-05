#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
2026-05-05 礎: subprocess を呼ぶ際の cmd window flash 抑制 helper

【背景】
2026-05-05 16:43 に CEO が HOST PC で「コマンド画面が何回も立ち上がっては閉じる」
現象を視認。原因は subprocess.run() / Popen() で console application
(VBoxManage.exe, python.exe, schtasks.exe 等) を呼ぶ際、Windows のデフォルトで
新規 cmd window が flash することだった。

vm_follow_launcher.py が 1 launch につき VBoxManage を 100-300 回 call するため、
launcher 起動中に CEO 視野に大量の cmd window flash が発生していた。

【解決】
本 helper の run_silent() / popen_silent() を経由することで CREATE_NO_WINDOW を
自動付与し、cmd window flash を完全抑制する。Linux では no-op。

【使い方】
    from shared.subprocess_helper import run_silent, popen_silent
    r = run_silent([VBOXMANAGE, "list", "runningvms"], capture_output=True, text=True)
    p = popen_silent([sys.executable, "script.py"], creationflags=DETACHED_PROCESS)
"""
from __future__ import annotations

import subprocess

# Windows でのみ有効。Linux/macOS では 0 (no-op)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_silent(cmd, **kwargs):
    """subprocess.run wrapper that suppresses cmd window flash on Windows.

    Existing creationflags があればそれに NO_WINDOW を OR する.
    """
    flags = kwargs.get("creationflags", 0) | NO_WINDOW
    kwargs["creationflags"] = flags
    return subprocess.run(cmd, **kwargs)


def popen_silent(cmd, **kwargs):
    """subprocess.Popen wrapper that suppresses cmd window flash on Windows."""
    flags = kwargs.get("creationflags", 0) | NO_WINDOW
    kwargs["creationflags"] = flags
    return subprocess.Popen(cmd, **kwargs)


# Convenience constant (re-export)
__all__ = ["NO_WINDOW", "run_silent", "popen_silent"]
