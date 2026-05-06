#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VM Follow Launcher (host-side, 48回運用)
HOST側からVBoxManage keyboardputscancodeでVM内の follow_rpa_v1.py を起動する。

設計:
  - VM のパスワード不明で guestcontrol 使用不可のための代替
  - 既存ログ(\\VBOXSVR\share\follow_rpa_log.json)が40分以内に更新されていれば skip
  - Chrome がフォロワーリストを表示中である前提（状態崩れたら手動復旧）
  - 毎時+00 に Windows タスクスケジューラから呼び出す

実行順:
  1. VM 稼働確認
  2. ログ鮮度チェック（40分以内 → skip）
  3. Win+D でデスクトップ表示
  4. Win+R → cmd + Enter
  5. cd Desktop + Enter
  6. cd bot + Enter
  7. python follow + TAB×3 → follow_rpa_v1.py
  8. " --limit 50" + Enter
  9. Alt+Tab で Chrome 前面化
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

VBOXMANAGE = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VM_NAME = "RoomBot"
REPO_ROOT = Path(__file__).resolve().parents[1]
LOG_PATH = REPO_ROOT / "rakuten-room" / "bot" / "executor" / "follow_rpa_log.json"
LAUNCH_LOG = REPO_ROOT / "ops" / "vm_follow_launcher.log"
LAST_LAUNCH_MARKER = REPO_ROOT / "ops" / "vm_follow_last_launch.txt"
COOLDOWN_MARKER = REPO_ROOT / "ops" / "vm_follow_cooldown.txt"
FRESH_THRESHOLD_MIN = 25
# Cooldown: config.FOLLOW_RL_COOLDOWN_MIN から読み込む (実データ確定値=69min)
try:
    import sys as _sys
    _sys.path.insert(0, str(REPO_ROOT / "rakuten-room" / "bot"))
    import config as _roombot_cfg
    COOLDOWN_MIN = _roombot_cfg.FOLLOW_RL_COOLDOWN_MIN
except Exception:
    COOLDOWN_MIN = 69  # fallback (実データ: 回復中央値60min + バッファ9min)
STAGED_RECOVERY_LIMIT = 20  # クールダウン明け初回のstaged recovery件数
FOLLOW_LIMIT = 200  # 2026-04-26 CEO指示: 毎日2000件必達のため 100→200 引上げ
# !! 重要 !! rate_limit 検知時は follow_rpa 内で Chrome auto-close する設計。
# launcher 側で rate_limit を再観測して新規 fire を skip する判断は patrol_with_sheet_sync.py 側で実装。
# 上限到達 = 即停止 のルール (CEO指示 2026-04-26) を絶対遵守する。
RAKUTEN_ROOM_HOME = "https://room.rakuten.co.jp/"


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LAUNCH_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# 2026-05-05 礎: HOST 上で subprocess を呼ぶ時、cmd window が一瞬 flash する現象 (CEO 目視) を完全抑制。
# 1 launch につき VBoxManage が 100-300 回 call されるため、CREATE_NO_WINDOW なしでは
# 連続で cmd window が立ち上がっては閉じる現象が CEO 視野に入る。
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


def run_vbox(*args) -> tuple[int, str]:
    try:
        r = subprocess.run(
            [VBOXMANAGE, *args],
            capture_output=True, text=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def vm_running() -> bool:
    rc, out = run_vbox("list", "runningvms")
    return rc == 0 and f'"{VM_NAME}"' in out


def wait_vm_ready(timeout: int = 90) -> bool:
    """VM 起動後 GuestAdditionsRunLevel=3 になり IME 切替が完了するまで待機.

    2026-05-05 礎: VM reset 直後 GuestAdditionsRunLevel=3 になっても、
    IME 切替が完了しないと keystroke が文字化けする (16:43 launch fail 実例)。
    GuestAdditionsRunLevel=3 を確認後 +30秒 cushion で IME 完全切替を待つ。

    Note: \\\\VBOXSVR\\share は VM 内専用 path で HOST から見えないため、
    VBoxManage showvminfo でVM側の起動段階を判定する.
    """
    deadline = time.time() + timeout
    started_at = time.time()
    while time.time() < deadline:
        rc, out = run_vbox("showvminfo", VM_NAME, "--machinereadable")
        if rc == 0:
            for line in (out or "").splitlines():
                if line.startswith("GuestAdditionsRunLevel="):
                    level = line.split("=", 1)[1].strip().strip('"')
                    if level == "3":
                        elapsed = int(time.time() - started_at)
                        log(f"  [wait_vm_ready] GuestAdditionsRunLevel=3 達成 ({elapsed}s)")
                        # IME 完全切替の cushion (+30秒)
                        log(f"  [wait_vm_ready] IME切替待機 +30s")
                        time.sleep(30)
                        return True
                    break
        time.sleep(3)
    log(f"  [wait_vm_ready] TIMEOUT {timeout}s — RunLevel<3 (VM起動異常)")
    return False


def last_launch_recent() -> bool:
    """前回のlauncher起動から40分以内なら True（= まだ前回実行中の可能性）"""
    if not LAST_LAUNCH_MARKER.exists():
        return False
    age_sec = time.time() - LAST_LAUNCH_MARKER.stat().st_mtime
    return age_sec < FRESH_THRESHOLD_MIN * 60


def write_marker():
    try:
        LAST_LAUNCH_MARKER.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception:
        pass


def scancode(*codes):
    run_vbox("controlvm", VM_NAME, "keyboardputscancode", *codes)


def putstr(s):
    run_vbox("controlvm", VM_NAME, "keyboardputstring", s)


# JP keyboard-aware injection (2026-04-20 12:xx fix):
# VBoxManage keyboardputstring assumes US layout. When the VM's Windows is set to
# Japanese, several US chars map to the wrong JP keys:
#   \ (US 0x2B) → VM outputs ]        _ (US shift+0x0C) → VM outputs =
#   : (US shift+0x27) → VM outputs +  " sometimes becomes *
# Workaround: send these chars via keyboardputscancode using JP-correct scancodes.
JP_SCANCODE_MAP = {
    "\\": ("7d", "fd"),                       # ¥ key → backslash on JP layout
    ":":  ("28", "a8"),                       # : key unshifted on JP layout
    "_":  ("2a", "73", "f3", "aa"),          # shift + ろ key → _
    '"':  ("2a", "03", "83", "aa"),          # shift + 2 → " on JP (US shift+2 = @)
}


def putstr_jp(s: str):
    """putstr with per-char JP-keyboard fallback for \\ : _ chars."""
    buffer = []
    def flush():
        if buffer:
            run_vbox("controlvm", VM_NAME, "keyboardputstring", "".join(buffer))
            buffer.clear()
            time.sleep(0.05)
    for ch in s:
        codes = JP_SCANCODE_MAP.get(ch)
        if codes is None:
            buffer.append(ch)
        else:
            flush()
            scancode(*codes)
            time.sleep(0.05)
    flush()


def last_stop_reason():
    """follow_rpa_log.json の最新エントリから stop_reason を取得"""
    try:
        if not LOG_PATH.exists():
            return None, None
        logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if not logs:
            return None, None
        latest = logs[-1]
        ts = datetime.fromisoformat(latest.get("timestamp", "").split(".")[0])
        return latest.get("stop_reason"), ts
    except Exception as e:
        log(f"WARN: last_stop_reason read failed: {e}")
        return None, None


def recent_rate_limit(scan_count: int = 12):
    """直近 scan_count エントリから rate_limit_detected を探す。
    間に別reason（no_button_detected 等）を挟んでも検出できる。
    (ts, age_min) を返す。見つからなければ (None, None)。"""
    try:
        if not LOG_PATH.exists():
            return None, None
        logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if not logs:
            return None, None
        for entry in reversed(logs[-scan_count:]):
            if entry.get("stop_reason") == "rate_limit_detected":
                ts = datetime.fromisoformat(entry.get("timestamp", "").split(".")[0])
                age_min = (datetime.now() - ts).total_seconds() / 60
                return ts, age_min
        return None, None
    except Exception as e:
        log(f"WARN: recent_rate_limit read failed: {e}")
        return None, None


def check_cooldown() -> tuple[bool, float]:
    """rate_limitクールダウン中か判定。(is_cooling, remain_min) を返す。"""
    if not COOLDOWN_MARKER.exists():
        return False, 0.0
    age_sec = time.time() - COOLDOWN_MARKER.stat().st_mtime
    remain_min = COOLDOWN_MIN - (age_sec / 60)
    return age_sec < COOLDOWN_MIN * 60, max(0.0, remain_min)


def write_cooldown_marker():
    try:
        COOLDOWN_MARKER.write_text(datetime.now().isoformat(), encoding="utf-8")
        log(f"COOLDOWN marker written ({COOLDOWN_MIN} min)")
    except Exception:
        pass


def launch_chrome():
    """VM内でChromeが前面にない場合に起動＋Rakuten ROOMへnavigate"""
    log("Chrome auto-launch sequence start")
    # Win+D → desktop (safe baseline)
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.0)
    # Edge kill (CEO指示 2026-04-20: Edgeが前面に出ないよう強制kill)
    scancode("e0", "5b", "13", "93", "e0", "db")  # Win+R
    time.sleep(1.5)
    putstr("cmd /c taskkill /IM msedge.exe /F /T")
    time.sleep(0.3)
    scancode("1c", "9c")  # Enter
    time.sleep(2.0)
    # Win+D again (ensure desktop baseline)
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.0)
    # Win+R → run dialog
    scancode("e0", "5b", "13", "93", "e0", "db")
    time.sleep(1.5)
    # type "chrome" + Enter
    putstr("chrome")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(6.0)  # Chrome起動待ち
    # Ctrl+L → focus URL bar
    scancode("1d", "26", "a6", "9d")
    time.sleep(0.8)
    # Type Rakuten ROOM URL
    putstr(RAKUTEN_ROOM_HOME)
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(4.0)  # ページロード待ち
    log("Chrome launch + navigate done")


def notify_previous_rate_limit():
    """前回runでrate_limit検知があればスクショをSlackへ自動通知（CEO指示 2026-04-20）"""
    try:
        script = Path(__file__).parent / "notify_rate_limit.py"
        if script.exists():
            r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
            if r.stdout:
                log(f"notify_rate_limit: {r.stdout.strip().splitlines()[-1] if r.stdout.strip() else 'no output'}")
            if r.returncode != 0 and r.stderr:
                log(f"notify_rate_limit stderr: {r.stderr.strip()[:200]}")
    except Exception as e:
        log(f"WARN: notify_rate_limit failed: {e}")


def _calc_c24_now(data: list) -> int:
    """現時点の直近24h成功合計を計算する"""
    from datetime import timezone
    now = datetime.now()
    cutoff = now - timedelta(hours=24)
    total = 0
    for entry in data:
        try:
            ts = datetime.fromisoformat(entry.get("timestamp", "2000-01-01"))
        except Exception:
            continue
        if ts >= cutoff:
            total += entry.get("success", 0)
    return total


def check_dead_zone() -> tuple[bool, str]:
    """
    ⑦ Dead Zone自動検知 (2026-05-03 実装 / bugfix同日)
    直近2セッションが dead_zone パターンに一致する場合 True を返す。

    dead_zone判定基準:
      A) seed枯渇型: modal_open_failed > 100 AND success == 0
         → 全seedがMAX_CONSECUTIVE_SKIP=30で即終了（フォロー中ボタンだらけ）
      B) RL dead zone型: rate_limit_detected > 10 AND success == 0
         → ボタンは見えるが楽天がすべてRL返し（c24過多）
      C) all_seeds_done with high already_followed: already_followed > 200 AND success == 0
         → ほぼ全userが既フォロー済み（seed_fresh不足）

    注: total_attempts フィールドは常に0なので fail_stats で判定する

    バイパス条件 (c24ロールオフ回復):
      直近dead_zoneセッション以降にc24が C24_RECOVERY_DROP 以上減少していれば
      dead_zone判定をバイパスする。c24ロールオフによる自然回復を見逃さないため。
      C24_RECOVERY_DROP = 80件（約1セッション分のロールオフ相当）

    戻り値: (is_dead: bool, reason: str)
    --force で呼び出す場合はこの関数を skip すること。
    """
    C24_RECOVERY_DROP = 80  # この件数以上c24が減少したら回復可能と判断

    try:
        if not LOG_PATH.exists():
            return False, "log not found"
        with open(LOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if len(data) < 2:
            return False, f"insufficient data ({len(data)} entries)"

        recent = data[-3:]  # 直近3セッション
        dead_count = 0
        reasons = []
        last_dead_ts = None

        for entry in recent:
            s = entry.get("success", 0)
            fail_stats = entry.get("fail_stats", {})
            rl = fail_stats.get("rate_limit_detected", 0)
            modal_fail = fail_stats.get("modal_open_failed", 0)
            already_followed = fail_stats.get("already_followed", 0)
            ts_str = entry.get("timestamp", "?")
            ts = ts_str[:16]

            if s > 0:
                # 成功あり → dead zone ではない
                continue

            # A) seed枯渇型
            if modal_fail > 100:
                dead_count += 1
                reasons.append(f"{ts}:seed枯渇(modal={modal_fail})")
                last_dead_ts = ts_str
                continue

            # B) RL dead zone型
            if rl > 10:
                dead_count += 1
                reasons.append(f"{ts}:RL_block(RL={rl})")
                last_dead_ts = ts_str
                continue

            # C) 高already_followed型
            if already_followed > 200:
                dead_count += 1
                reasons.append(f"{ts}:全済フォロー(af={already_followed})")
                last_dead_ts = ts_str

        if dead_count < 2:
            return False, f"dead_zone={dead_count}/{len(recent)}: {', '.join(reasons) if reasons else 'OK'}"

        # dead_zone パターン確認 — c24ロールオフバイパスチェック
        if last_dead_ts:
            try:
                now = datetime.now()
                c24_now = _calc_c24_now(data)

                # dead_zoneセッション時点のc24を計算 (そのセッション完了時点)
                dead_ts = datetime.fromisoformat(last_dead_ts)
                cutoff_at_dead = dead_ts - timedelta(hours=24)
                c24_at_dead = sum(
                    e.get("success", 0) for e in data
                    if cutoff_at_dead <= datetime.fromisoformat(e.get("timestamp", "2000-01-01")) <= dead_ts
                )

                c24_drop = c24_at_dead - c24_now
                if c24_drop >= C24_RECOVERY_DROP:
                    return False, (
                        f"dead_zone検知だがc24ロールオフ回復中: "
                        f"c24_at_dead={c24_at_dead}→now={c24_now} (-{c24_drop}件≥{C24_RECOVERY_DROP}閾値) "
                        f"→ launch許可"
                    )
            except Exception as _be:
                pass  # バイパス計算失敗 → 通常のdead_zone判定を使う

        return True, f"直近{len(recent)}件中{dead_count}件dead_zone: {', '.join(reasons)}"
    except Exception as e:
        return False, f"check_dead_zone error: {e}"


def launch(force: bool = False, limit: int | None = None):
    log("=== launch start ===")

    # 前回runのrate_limitスクショをSlackへ通知（CEO指示 2026-04-20）
    notify_previous_rate_limit()

    if not vm_running():
        log("ABORT: VM not running")
        return 2

    # 2026-05-05 礎: VM 起動直後 GuestAdditionsRunLevel=3 でも IME 切替が間に合わず
    # keystroke 文字化け → python 起動失敗のケースあり (16:43 launcher fail 実例)
    # share folder 接続を確認することで VM 完全起動を判定し、+10秒 cushion で IME 切替も待つ
    if not wait_vm_ready(timeout=90):
        log("ABORT: VM share folder unreachable (IME 切替未完 or VM起動異常)")
        return 5

    # 2026-05-06 CEO指示: 24時間稼働ルール採用。dead_zone check を default bypass に変更。
    # rate_limit 検知時の Chrome 自動 close + cooldown が独立で機能するため、
    # 時刻独立の dead_zone check は撤廃して常時稼働を優先。
    log("[DEAD_ZONE_CHECK] 24h稼働ルール (CEO指示) で常時 bypass")
    # 旧ロジック (--force でのみ起動) は削除済

    # --- 制限なし運用（CEO指示 2026-04-21）---
    # rate_limit検知時のChrome自動close＋1時間インターバル自体が安全装置として十分に効いているため、
    # 以下の全skip/staged-recovery/fresh-threshold判定を無効化する。
    # - 60分rate_limitクールダウン: 削除
    # - staged recovery (件数抑制): 削除
    # - 25分fresh-threshold: 削除
    # 残す: VM 稼働確認のみ（上の if not vm_running() で済み）。
    # 古い COOLDOWN_MARKER が残っていたら掃除する（判定は使わない）。
    if COOLDOWN_MARKER.exists():
        try:
            COOLDOWN_MARKER.unlink()
        except Exception:
            pass

    # --- 24h窓ゾーン認識ログ (情報収集フェーズ 2026-05-02) ---
    # v2 時刻帯×累積帯 オプティマイザーチェック (2026-05-02 更新)
    # 情報収集フェーズ: ブロックしない。ログ + 推奨だけ残す。
    try:
        import subprocess as _sp
        _opt = REPO_ROOT / "ops" / "scripts" / "follow_optimizer.py"
        if _opt.exists():
            _r = _sp.run(
                [sys.executable, str(_opt), "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15
            )
            if _r.returncode == 0:
                import json as _json
                _info = _json.loads(_r.stdout)
                _c24 = _info.get("current_24h", 0)
                _zone = _info.get("zone", "?")
                _pred = _info.get("next_optimal", {})
                _ctx  = _info.get("time_context", {})
                _rec  = _ctx.get("recommendation", "unknown")
                _suc_pct = _ctx.get("expected_success_pct")
                _tb   = _ctx.get("time_band_jp", "?")
                _cb   = _ctx.get("c24_band", "?")
                log(f"[ZONE_v2] 24h={_c24} zone={_zone} 時刻帯={_tb} 累積帯={_cb} "
                    f"期待成功率={_suc_pct}% 推奨={_rec}")
                # 推奨別ログ (情報収集フェーズ: ブロックしない)
                if _rec == "go":
                    log(f"[ZONE_GO] 最良の組み合わせで起動。期待成功率{_suc_pct}%")
                elif _rec == "caution":
                    log(f"[ZONE_CAUTION] やや低め({_suc_pct}%)だが続行")
                elif _rec == "wait_or_accept_rl":
                    log(f"[ZONE_RISKY] 低成功率({_suc_pct}%)。RL覚悟で続行 (情報収集フェーズ)")
                elif _rec == "stop":
                    log(f"[ZONE_STOP_REC] 推奨=stop (期待成功率{_suc_pct}%)だが情報収集フェーズのため続行")
                if _pred.get("status") == "wait_recommended":
                    log(f"[ZONE_OPT] {_pred.get('expiry_at')}({_pred.get('wait_hours','?')}h後)が最適起動 (現在={_c24}件)")
    except Exception as _ze:
        log(f"WARN: zone_check failed: {_ze}")

    # --- limit の決定（③ 朝抑制付き dynamic limit 2026-05-03）---
    # 手動 --limit 指定がある場合はそちらを優先（force=True 含む）
    # 2026-05-06 CEO指示: 24時間稼働ルール採用。朝抑制 (MORNING_LIMIT 06:00-10:59) 撤廃。
    # 全時間帯で FOLLOW_LIMIT (200) を使用。c24 過多防止は dead_zone bypass 化で対応。
    _now_hour = datetime.now().hour
    if limit is not None:
        use_limit = limit
    else:
        use_limit = FOLLOW_LIMIT
        log(f"[24h稼働] {_now_hour:02d}時台 → limit={FOLLOW_LIMIT} (CEO指示 朝抑制撤廃)")

    write_marker()

    # --- 新設計 (2026-04-20 11:13 bugfix) ---
    # 旧: launch_chrome() が Chrome を先に起動→その後 Win+R→cmd だが、
    #     Chromeがフォアグラウンドに居座り Win+R を URL bar に吸収され Open File dialog 発生（10:00/11:00 fire で連続失敗）
    # 新: Win+D で必ずデスクトップに戻し、Win+R→cmd を最初に実行。
    #     cmd 内で taskkill → cd → copy → start chrome URL → timeout → python follow を1本の入力ストリームで発火。
    #     Chrome は cmd の start コマンドで開くため、ホスト側scancodeがChromeに吸われる機会がない。

    # 0) Win+D ×2 でデスクトップを確実に前面化（残存ウィンドウが何であれ最小化）
    # 2026-04-22 09:43 rollback: Alt+F4 は desktop 上で Shutdown dialog を出すため撤回。
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.0)
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.5)

    # 1) Win+R → run dialog
    scancode("e0", "5b", "13", "93", "e0", "db")
    time.sleep(2.0)

    # 2) "cmd" + Enter（Win+R に直接送るのでChrome吸収の心配なし）
    putstr("cmd")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(3.0)

    # 3) cmd内で一連の処理を発火
    # 3-0) QuickEdit / InsertMode をOFF（pyautogui のクリックがcmd選択モードを
    #      トリガーしてpython stdoutをブロックする問題を根絶）
    #      本コマンドは HKCU\\Console に書き込むため NEXT cmd 起動から有効
    putstr_jp('reg add "HKCU\\Console" /v QuickEdit /t REG_DWORD /d 0 /f')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.8)
    putstr_jp('reg add "HKCU\\Console" /v InsertMode /t REG_DWORD /d 0 /f')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.8)

    # 3-1) Chrome/Edge/python を kill（前回残存を一掃）
    #      NOTE: putstr で & を送ると ` に化けるため、コマンドを3回に分ける
    putstr("taskkill /IM msedge.exe /F /T")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.2)
    putstr("taskkill /IM chrome.exe /F /T")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.5)
    putstr("taskkill /IM python.exe /F /T")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.2)

    # 3-2) cd Desktop\bot  — JP kbd: \ 使用のため putstr_jp
    putstr_jp("cd /d %USERPROFILE%\\Desktop\\bot")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.6)

    # 3-3) 共有フォルダから最新版 follow_rpa_vm.py を同期  — JP kbd: \ _ 使用
    putstr_jp('copy /Y "\\\\vboxsvr\\share\\follow_rpa_vm.py" .')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.2)

    # 3-3a') 2026-05-05: seed_users.json も最新版を同期（HOSTが scrape マージ修正したため）
    # 過去事故: VM側の旧 seed_users.json が空（5ジャンル0件）で「No seed users」エラー連発
    putstr_jp('copy /Y "\\\\vboxsvr\\share\\seed_users.json" .')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.0)

    # 3-3b) Chrome の session状態ファイルを削除（tab restore 根絶・CEO指示 2026-04-22）
    # "Current Session"/"Current Tabs"/"Last Session"/"Last Tabs" が残っていると
    # --disable-session-crashed-bubble があっても古いタブ群が復元されてしまう。
    # Chrome 起動前に削除すれば次回起動時に復元対象ゼロ。
    for fname in ("Current Session", "Current Tabs", "Last Session", "Last Tabs"):
        putstr_jp(f'del /Q /F "%LOCALAPPDATA%\\Google\\Chrome\\User Data\\Default\\{fname}" 2>nul')
        time.sleep(0.2)
        scancode("1c", "9c")
        time.sleep(0.3)

    # 3-3c) Chrome Preferences を事前修正して「ページを復元しますか？」バブル抑止
    # CEO指示 2026-04-22: 前回Chrome force-kill で exited_cleanly=False が残り restore prompt が出るのを抑制
    putstr_jp('python "\\\\vboxsvr\\share\\fix_chrome_preferences.py"')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.5)

    # 3-3d) File Explorer / Recycle Bin 等のシェル窓を全て閉じる
    # 2026-04-22 09:33 patrol bug fix: Win+D ×2 では残存窓が再表示される場合があり、
    # Chrome 起動後に他窓が foreground を奪って pyautogui のキーストロークが
    # Recycle Bin の address bar に吸われる事象を 09:33 検証で観測。
    # Shell.Application.Windows() を Quit() することで全 Explorer 窓を安全に閉じる
    # （shell 本体 explorer.exe は kill しない）。
    putstr_jp('powershell -NoProfile -Command "(New-Object -ComObject Shell.Application).Windows() | ForEach-Object { $_.Quit() }"')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(2.0)

    # 3-4) Chrome を cmd から起動（非ブロッキング）→ Rakuten ROOM へ navigate  — JP kbd: : 使用
    # flag は保険：Preferences patch が効かない機でも bubble を非表示にする。
    putstr_jp(f'start "" chrome --new-window --disable-session-crashed-bubble --hide-crash-restore-bubble --no-first-run --disable-infobars {RAKUTEN_ROOM_HOME}')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.5)

    # 3-5) Chromeロード待ち（host側 sleep のみ・VM cmd への timeout 発行は省略）
    time.sleep(10.5)

    # 3-5a) 万一 restore bubble が表示されていた場合の保険として Escape を2回送る
    # Chrome フォアグラウンド前提（start直後は Chrome が取っているはず）
    scancode("01", "81")
    time.sleep(0.4)
    scancode("01", "81")
    time.sleep(0.4)

    # 3-5b) Chrome が foreground を奪ったので Alt+Tab で cmd に戻す
    #       （これをしないと python のキーストロークが Chrome に吸われる）
    scancode("38", "0f", "8f", "b8")
    time.sleep(0.8)

    # 3-6) python follow_rpa_vm.py 発火  — JP kbd: _ 使用
    #   2026-04-22 09:33 pythonw化 rollback:
    #   pythonw + exit で cmd を閉じると Z-order で他窓（File Explorer/Recycle Bin等）が
    #   foreground 奪取。09:33 検証で Recycle Bin address bar に URL typed を観測。
    #   従って python（console あり）+ follow_rpa_vm.py 内の _minimize_console_window()
    #   毎navigate call 方式に回帰（08:12 screenshot で実証済み動作）。
    # 2026-05-05: launcher --force を VM側スクリプトに伝達（dead_zone bypass）
    force_arg = " --force" if force else ""
    putstr_jp(f"python follow_rpa_vm.py --limit {use_limit}{force_arg}")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.8)

    # 4) Alt+Tab で Chrome を前面化（pyautogui が画像認識でクリックするため）
    scancode("38", "0f", "8f", "b8")

    log(f"OK: follow bot launched (limit={use_limit}, force={force})")

    # 2026-05-05 Phase 2-4: launch verification
    # 起動から最大90秒以内に heartbeat が出ていれば成功 / 出なければ launch 失敗を Slack通知
    _verify_launch_via_heartbeat(timeout_sec=90)
    return 0


def _verify_launch_via_heartbeat(timeout_sec: int = 90) -> None:
    """launcher exit 前に「VM内 follow_rpa_vm.py が実際に起動したか」を heartbeat で検証。

    HEARTBEAT_SHARE = ROOT/rakuten-room/bot/executor/follow_heartbeat.json
    timeout_sec 内に heartbeat ts が「launch開始時刻」より新しくなれば OK。
    なければ Slack <!here> 通知 + screenshot保存（VBoxManage screenshotpng）。
    """
    import json as _json
    from datetime import datetime as _dt
    hb_path = Path(r"C:\Users\infoa\Documents\solarworks-ai\rakuten-room\bot\executor\follow_heartbeat.json")
    launch_start = _dt.now()
    deadline = time.time() + timeout_sec
    log(f"  [verify] heartbeat polling (timeout={timeout_sec}s)")
    while time.time() < deadline:
        time.sleep(5)
        if not hb_path.exists():
            continue
        try:
            hb = _json.loads(hb_path.read_text(encoding="utf-8"))
            hb_ts = _dt.fromisoformat(hb.get("ts", ""))
            if hb_ts >= launch_start:
                log(f"  [verify] OK: heartbeat at {hb_ts.isoformat()} phase={hb.get('phase','?')}")
                return
        except Exception:
            continue
    # timeout
    log(f"  [verify] FAIL: no heartbeat within {timeout_sec}s — launch failed")
    # screenshot 保存
    ss_path = Path(r"C:\Users\infoa\Documents\solarworks-ai\ops\patrol_screenshots") / f"_launch_fail_{launch_start.strftime('%Y%m%d_%H%M%S')}.png"
    ss_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([VBOXMANAGE, "controlvm", "RoomBot", "screenshotpng", str(ss_path)], capture_output=True, timeout=20, creationflags=_NO_WINDOW)
    except Exception:
        pass
    # Slack通知
    try:
        slack = Path(r"C:\Users\infoa\Documents\solarworks-ai\ops\notifications\slack_reporter.py")
        msg = (
            f"<!here> 【launcher緊急】follow bot 起動失敗 ({launch_start.strftime('%Y-%m-%d %H:%M:%S')})\n"
            f"VM内Pythonがheartbeatを{timeout_sec}秒以内に出力しませんでした。\n"
            f"スクショ: {ss_path}\n"
            f"次の対応: cmd窓内エラー確認 / VM再起動 / login確認"
        )
        subprocess.run([sys.executable, str(slack), msg], capture_output=True, timeout=30, creationflags=_NO_WINDOW)
    except Exception as e:
        log(f"  [verify] Slack notify failed: {e}")


def launch_scrape() -> int:
    """seed_users.json を更新する --scrape セッションを VM 内で起動する。
    follow launch() と同じ scancode 方式。Chrome は scrape_seed_users() が自動起動するので
    launcher 側での Chrome 起動・Alt+Tab は不要。
    """
    log("=== launch_scrape: start ===")

    if not vm_running():
        log("SKIP: VM not running")
        return 1

    # 0) Win+D ×2 でデスクトップ前面化
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.0)
    scancode("e0", "5b", "20", "a0", "e0", "db")
    time.sleep(1.5)

    # 1) Win+R → cmd
    scancode("e0", "5b", "13", "93", "e0", "db")
    time.sleep(2.0)
    putstr("cmd")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(3.0)

    # 2) QuickEdit / InsertMode OFF
    putstr_jp('reg add "HKCU\\Console" /v QuickEdit /t REG_DWORD /d 0 /f')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.8)
    putstr_jp('reg add "HKCU\\Console" /v InsertMode /t REG_DWORD /d 0 /f')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.8)

    # 3) 既存 Chrome / python を kill
    putstr("taskkill /IM chrome.exe /F /T")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.5)
    putstr("taskkill /IM python.exe /F /T")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.2)

    # 4) cd Desktop\bot
    putstr_jp("cd /d %USERPROFILE%\\Desktop\\bot")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.6)

    # 5) 共有フォルダから最新版をコピー
    putstr_jp('copy /Y "\\\\vboxsvr\\share\\follow_rpa_vm.py" .')
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(1.2)

    # 6) python follow_rpa_vm.py --scrape 発火
    #    Chrome は scrape_seed_users() 内で自動起動されるため launcher 側での起動不要
    putstr_jp("python follow_rpa_vm.py --scrape")
    time.sleep(0.3)
    scancode("1c", "9c")
    time.sleep(0.5)

    log("OK: scrape launched")
    return 0


if __name__ == "__main__":
    force = "--force" in sys.argv
    scrape = "--scrape" in sys.argv
    limit_override = None
    if "--limit" in sys.argv:
        i = sys.argv.index("--limit")
        if i + 1 < len(sys.argv):
            limit_override = int(sys.argv[i + 1])
    try:
        if scrape:
            sys.exit(launch_scrape())
        else:
            sys.exit(launch(force=force, limit=limit_override))
    except Exception as e:
        log(f"ERROR: {e}")
        sys.exit(3)
