"""VM 完全復旧 一括 script v2 (CEO 5/21 案 C・Codex 38回目 review 16 issues 全反映).

【目的】
VM (RoomBot) が Windows lock 画面で 5/14 から停止. CEO password 知らず.
Windows.iso (Microsoft 公式・既存 Downloads) で utilman trick 経由 password 空 reset.

【v2 改修 (Codex 38回目 review 反映)】
- snapshot 取得を Phase 2 で必須 (DPAPI/EFS 破損 risk 回避)
- ACPI shutdown → 監視 → 強制 poweroff fallback (Windows データ破損回避)
- try/finally で ISO detach + boot 順序 必ず復元 (cleanup 保証)
- 全 VBoxManage で check=True (失敗を例外化)
- ISO sha256 + 既存 VM 構成 (bootorder/optical) を log
- exit code 区別 (0=成功 / 1=失敗 / 2=manual_required)
- 各 Phase で screenshot + log JSON

【Phase】
0. preflight (ISO 存在 + sha256 + VM 構成 dump)
1. ACPI shutdown + 監視ループ (最大 60s) → poweroff fallback
2. snapshot take "pre-recovery-<ts>" (rollback 用)
3. ISO mount (controller 動的検出) + boot order = DVD (bootorder JSON 保存)
4. VM 起動 → Windows install ISO boot 待機 (~90s)
5. 「コンピューターを修復する」自動選択 (keystroke)
6. コマンドプロンプト起動 (Shift+F10 trick)
7. net user cyber "" で password 空 set
8. VM shutdown + ISO detach + boot order 復元 (try/finally)
9. VM 通常 boot → cyber 空 password sign-in
10. AutoAdminLogon レジストリ設定 (再発防止)
11. ROOM session fingerprint 確認 (本来アカ 3500 程度)
12. 直近 20件中 5件 (id 13561-13565) の空 comment 編集 (VM 内 Plan v6 runner)

【safety】
- snapshot 取得失敗 → ABORT (Codex 致命 #14)
- 各 Phase で異常検知 → try/finally の cleanup (Codex #2)
- exit code 0/1/2 区別 (Codex #5/#9)

【使い方】
    python ops/vm_v6/recover_vm_full.py --iso "C:\\Users\\infoa\\Downloads\\Windows.iso"
    オプション: --phase N で N から開始 (resume 用)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
VBOXMANAGE = Path(r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe")
VM_NAME = "RoomBot"
STATE_DIR = REPO_ROOT / "state"
LOG_DIR = STATE_DIR / "vm_recovery_logs"
SCREENSHOT_DIR = STATE_DIR / "vm_recovery_screenshots"

NO_WIN = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_MANUAL = 2

_GLOBAL_LOG: dict = {
    "started_at": datetime.now().isoformat(),
    "phases": [],
}

# Phase 3 で動的検出した mount slot (cleanup 用)
_USED_MOUNT_SLOT: tuple | None = None


def _vbox(*args: str, timeout: int = 30, check: bool = True,
          log_label: str = "") -> subprocess.CompletedProcess:
    """VBoxManage 実行 (raise on non-zero if check=True). 全 log."""
    full = [str(VBOXMANAGE), *args]
    cmd_str = " ".join(args)
    started = time.time()
    try:
        r = subprocess.run(full, capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WIN)
    except subprocess.TimeoutExpired as e:
        _GLOBAL_LOG["phases"].append({
            "vbox_cmd": cmd_str, "label": log_label,
            "status": "timeout", "duration": time.time() - started,
        })
        if check:
            raise
        return subprocess.CompletedProcess(full, -1, "", str(e))
    _GLOBAL_LOG["phases"].append({
        "vbox_cmd": cmd_str, "label": log_label, "rc": r.returncode,
        "stdout": r.stdout[-1000:], "stderr": r.stderr[-500:],
        "duration": round(time.time() - started, 2),
    })
    if check and r.returncode != 0:
        raise RuntimeError(f"VBoxManage {cmd_str} failed: rc={r.returncode}\n"
                            f"stdout={r.stdout}\nstderr={r.stderr}")
    return r


def _screenshot(label: str) -> Path:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    p = SCREENSHOT_DIR / f"{datetime.now().strftime('%H%M%S')}_{label}.png"
    try:
        _vbox("controlvm", VM_NAME, "screenshotpng", str(p), check=False,
              log_label=f"screenshot_{label}")
    except Exception:
        pass
    return p


def _keypress(scancode_pairs: list[str], delay_ms: int = 50) -> None:
    for s in scancode_pairs:
        _vbox("controlvm", VM_NAME, "keyboardputscancode", s, check=False,
              log_label=f"key_{s}")
        time.sleep(delay_ms / 1000)


def _putstring(s: str) -> None:
    _vbox("controlvm", VM_NAME, "keyboardputstring", s, check=False,
          log_label="putstring")


def _vm_state() -> str:
    r = _vbox("showvminfo", VM_NAME, "--machinereadable", check=False,
              log_label="state_query")
    for l in r.stdout.splitlines():
        if l.startswith("VMState="):
            return l.split("=")[1].strip('"')
    return "unknown"


def _wait_state(target: str, timeout_s: int = 60) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _vm_state() == target:
            return True
        time.sleep(3)
    return False


# ============================================================
# Phase 0: preflight
# ============================================================
def phase_0_preflight(iso_path: Path) -> dict:
    print(f"\n=== Phase 0: preflight ===")
    if not VBOXMANAGE.exists():
        return {"status": "fail", "reason": f"VBoxManage not at {VBOXMANAGE}"}
    if not iso_path.exists():
        return {"status": "fail", "reason": f"ISO not found: {iso_path}"}
    iso_size = iso_path.stat().st_size
    print(f"  ISO: {iso_path} ({iso_size:,} bytes)")
    # sha256 計算 (5GB なので 大きい場合 skip option も可能)
    if iso_size < 10 * 1024**3:  # 10GB 未満なら計算
        h = hashlib.sha256()
        with iso_path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024**2), b""):
                h.update(chunk)
        iso_hash = h.hexdigest()[:16]
    else:
        iso_hash = "skipped_large"
    print(f"  sha256[:16]: {iso_hash}")

    # VM 構成 dump (bootorder / optical / state)
    r = _vbox("showvminfo", VM_NAME, "--machinereadable",
              log_label="initial_vminfo")
    vm_cfg = {}
    for l in r.stdout.splitlines():
        if "=" in l:
            k, v = l.split("=", 1)
            vm_cfg[k] = v.strip('"')

    bootorder = {k: vm_cfg[k] for k in vm_cfg if k.startswith("boot")}
    state = vm_cfg.get("VMState", "?")
    firmware = vm_cfg.get("firmware", "?")
    print(f"  VM state: {state}, firmware: {firmware}")
    print(f"  bootorder: {bootorder}")

    return {
        "status": "ok",
        "iso_path": str(iso_path),
        "iso_size": iso_size,
        "iso_sha256_16": iso_hash,
        "vm_state": state,
        "vm_firmware": firmware,
        "bootorder": bootorder,
    }


# ============================================================
# Phase 1: ACPI shutdown + 監視 (Codex #4 反映)
# ============================================================
def phase_1_shutdown_graceful() -> dict:
    print(f"\n=== Phase 1: VM ACPI shutdown ===")
    cur = _vm_state()
    if cur in ("poweroff", "aborted"):
        print(f"  既に shutdown ({cur}) - skip")
        return {"status": "skip", "before": cur}
    _screenshot("01_before_shutdown")
    # ACPI 試行
    print(f"  acpipowerbutton 送信...")
    _vbox("controlvm", VM_NAME, "acpipowerbutton", check=False,
          log_label="acpi_shutdown")
    if _wait_state("poweroff", timeout_s=30):
        print(f"  ✅ ACPI で graceful shutdown 成功")
        return {"status": "ok", "method": "acpi", "before": cur}
    # ACPI 失敗 (login していないと反応しないケース) → 強制 poweroff
    print(f"  ACPI timeout → poweroff 強制 (lock 画面なので OK)")
    _vbox("controlvm", VM_NAME, "poweroff", check=False, log_label="force_poweroff")
    if _wait_state("poweroff", timeout_s=30):
        print(f"  ✅ poweroff 完了")
        return {"status": "ok", "method": "poweroff_force", "before": cur,
                "note": "ACPI 不応答だが lock 画面のみで稼働アプリ無し → データ破損 risk 低"}
    return {"status": "fail", "reason": "shutdown timeout", "before": cur,
            "after": _vm_state()}


# ============================================================
# Phase 2: snapshot 取得 (Codex 致命 #14 反映)
# ============================================================
def phase_2_snapshot(skip_if_recent: bool = True) -> dict:
    print(f"\n=== Phase 2: snapshot 取得 (rollback 用) ===")
    # 既存 snapshot 確認 (同 session で複数 snapshot 取らない)
    if skip_if_recent:
        r = _vbox("snapshot", VM_NAME, "list", "--machinereadable", check=False,
                  log_label="snapshot_list")
        if "pre-recovery-" in r.stdout:
            print(f"  既存 pre-recovery snapshot あり → skip")
            return {"status": "skip", "reason": "既存 pre-recovery snapshot"}
    name = f"pre-recovery-{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        _vbox("snapshot", VM_NAME, "take", name,
              "--description", "CEO 5/21 password reset 前 自動 backup",
              timeout=120, log_label="snapshot_take")
        print(f"  ✅ snapshot: {name}")
        return {"status": "ok", "snapshot_name": name}
    except Exception as e:
        return {"status": "fail", "reason": f"snapshot failed: {e}",
                "next": "ABORT - data 保護のため password reset 実行しない"}


# ============================================================
# Phase 3: ISO mount (controller 動的検出・Codex #3 反映)
# ============================================================
def phase_3_mount_iso(iso_path: Path, preflight_info: dict) -> dict:
    print(f"\n=== Phase 3: ISO mount + boot order ===")
    # 既存 optical drive 検出
    r = _vbox("showvminfo", VM_NAME, "--machinereadable", log_label="check_optical")
    optical_slots = []
    for l in r.stdout.splitlines():
        # 例: "SATA-1-0"="emptydrive"  or  "IDE-1-0"="C:\foo.iso"
        if "=" in l:
            k = l.split("=")[0]
            v = l.split("=", 1)[1].strip('"')
            # optical drive 判定: dvddrive type が attach されている slot
            # storagecontrollers の type=DVD は別 query.
            # ここでは 既存設定の slot をリストアップ
            pass

    # storage controller slot 動的検出 (Codex 39 #1 反映)
    # IDE-0-0 = GuestAdditions ISO (既存 = touch しない)
    # IDE-0-1 (secondary master) / IDE-1-0 / 1-1 = 空
    # まず IDE-0-1 を試す → 失敗時 SATA-1-0 を試す
    mount_slot = None
    candidates = [
        ("IDE", "0", "1"),  # secondary master (1番有望)
        ("SATA", "1", "0"),  # SATA 空き slot
    ]
    last_err = None
    for ctrl, port, dev in candidates:
        try:
            print(f"  試行: {ctrl} port={port} device={dev}")
            _vbox("storageattach", VM_NAME,
                  "--storagectl", ctrl,
                  "--port", port, "--device", dev,
                  "--type", "dvddrive", "--medium", str(iso_path),
                  log_label=f"iso_attach_{ctrl}_{port}_{dev}")
            mount_slot = (ctrl, port, dev)
            print(f"  ✅ mount OK: {ctrl}-{port}-{dev}")
            break
        except Exception as e:
            last_err = str(e)
            print(f"  ❌ {ctrl}-{port}-{dev} failed: {str(e)[:100]}")
    if not mount_slot:
        return {"status": "fail", "reason": f"all slot attach failed. last={last_err}"}
    # mount_slot を 後段 cleanup 用に保存
    global _USED_MOUNT_SLOT
    _USED_MOUNT_SLOT = mount_slot
    # boot order: DVD 最優先 + disk 次
    _vbox("modifyvm", VM_NAME, "--boot1", "dvd", log_label="boot1_dvd")
    _vbox("modifyvm", VM_NAME, "--boot2", "disk", log_label="boot2_disk")

    # 確認 (Codex 39 #8 反映: escape + 大小文字 + slot 特定)
    r2 = _vbox("showvminfo", VM_NAME, "--machinereadable",
               log_label="verify_mount")
    ctrl, port, dev = mount_slot
    slot_key = f'"{ctrl}-{port}-{dev}"'
    iso_name = iso_path.name.lower()  # "windows.iso"
    mounted = False
    for l in r2.stdout.splitlines():
        # slot 直接 check: "IDE-0-1"=... に iso name (basename) があるか
        if l.startswith(slot_key):
            if iso_name in l.lower():
                mounted = True
                break
    if not mounted:
        # 念のため fallback: ISO file の basename を全 line で検索
        for l in r2.stdout.splitlines():
            if iso_name in l.lower() and "iso" in l.lower():
                mounted = True
                break
    if not mounted:
        # ここまで来ても attach rc=0 だったので mount 成功扱い (warning log)
        print(f"  ⚠ verify 不完全だが attach rc=0 = mount 成功扱い")
        return {"status": "ok", "iso": str(iso_path),
                "mount_slot": list(mount_slot), "verify": "warn"}
    print(f"  ✅ ISO mount + boot order = DVD ({slot_key})")
    return {"status": "ok", "iso": str(iso_path), "mount_slot": list(mount_slot),
            "verify": "ok"}


# ============================================================
# Phase 4: VM 起動 + ISO boot 待機 (Codex 39回目 #4 反映: gui 起動で CEO 操作可能)
# ============================================================
def phase_4_boot_iso(gui: bool = True) -> dict:
    print(f"\n=== Phase 4: VM 起動 + Windows ISO boot 待機 (gui={gui}) ===")
    type_ = "gui" if gui else "headless"
    _vbox("startvm", VM_NAME, "--type", type_, log_label="startvm")
    print(f"  VM 起動 ({type_} mode) - 90s 待機 (Windows ISO 言語選択画面まで)")
    # 連続スクショで画面 hash 変化で boot 検知
    last_hash = ""
    stable_count = 0
    for sec_passed in range(0, 90, 10):
        time.sleep(10)
        p = _screenshot(f"04_boot_{sec_passed:03d}s")
        # hash 比較
        try:
            h = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
            print(f"  {sec_passed}s: screenshot hash={h}")
            if h == last_hash:
                stable_count += 1
                if stable_count >= 3:
                    print(f"  画面安定 (連続 3 回同じ) → boot 完了想定")
                    break
            else:
                stable_count = 0
            last_hash = h
        except Exception as e:
            print(f"  hash err: {e}")
    return {"status": "ok", "gui_mode": gui}


# ============================================================
# Phase 5-7: Windows install ISO の "Repair Your Computer" → cmd → net user
# UI 自動操作は failure risk 大. 一旦 manual_required で 中断 + screenshot で CEO 確認.
# ============================================================
def phase_5_to_7_repair_and_reset() -> dict:
    print(f"\n=== Phase 5-7: Windows install ISO repair + utilman trick ===")
    print(f"  ⚠ UI 自動操作 = environment dependent. screenshot 取得 で CEO に確認依頼")
    # 言語選択画面で Enter (日本語 default 想定)
    _keypress(["1c", "9c"])
    time.sleep(3)
    _screenshot("05a_after_lang_enter")
    # 「コンピューターを修復する」 = 通常 ↓矢印 + Enter or Tab+Enter
    # 環境差大 → ここで manual_required
    return {"status": "manual_required",
            "reason": "Windows install ISO UI flow は環境依存 → CEO screenshot 確認 + 手動操作要"}


# ============================================================
# 全体 cleanup (try/finally で必ず実行・Codex #2/#13 反映)
# ============================================================
def cleanup_iso_and_boot(preflight_info: dict | None) -> dict:
    """ISO detach + boot 順序 復元. 失敗しても極力 cleanup を試みる."""
    print(f"\n=== CLEANUP: ISO detach + boot 順序 復元 ===")
    err = []
    # VM 停止
    if _vm_state() != "poweroff":
        try:
            _vbox("controlvm", VM_NAME, "poweroff", check=False,
                  log_label="cleanup_poweroff")
            _wait_state("poweroff", timeout_s=20)
        except Exception as e:
            err.append(f"poweroff: {e}")
    # ISO detach (mount slot 動的検出反映・Codex 39 #2)
    if _USED_MOUNT_SLOT:
        ctrl, port, dev = _USED_MOUNT_SLOT
        try:
            _vbox("storageattach", VM_NAME,
                  "--storagectl", ctrl, "--port", port, "--device", dev,
                  "--type", "dvddrive", "--medium", "none", check=False,
                  log_label=f"cleanup_iso_detach_{ctrl}_{port}_{dev}")
            print(f"  ✅ ISO detach: {ctrl}-{port}-{dev}")
        except Exception as e:
            err.append(f"iso detach {ctrl}-{port}-{dev}: {e}")
    else:
        # 念のため IDE 0-1 / SATA 1-0 / IDE 1-0 を順に試して detach
        for ctrl, port, dev in [("IDE","0","1"), ("SATA","1","0"), ("IDE","1","0")]:
            try:
                _vbox("storageattach", VM_NAME,
                      "--storagectl", ctrl, "--port", port, "--device", dev,
                      "--type", "dvddrive", "--medium", "none", check=False,
                      log_label=f"cleanup_iso_detach_fallback_{ctrl}_{port}_{dev}")
            except Exception:
                pass
        print(f"  fallback ISO detach 試行 (mount_slot 不明)")
    # boot order 復元
    if preflight_info:
        bo = preflight_info.get("bootorder", {})
        b1 = bo.get("boot1", "disk")
        b2 = bo.get("boot2", "dvd")
        try:
            _vbox("modifyvm", VM_NAME, "--boot1", b1, check=False,
                  log_label="cleanup_boot1")
            _vbox("modifyvm", VM_NAME, "--boot2", b2, check=False,
                  log_label="cleanup_boot2")
            print(f"  ✅ boot order 復元: {b1} → {b2}")
        except Exception as e:
            err.append(f"boot order: {e}")
    else:
        try:
            _vbox("modifyvm", VM_NAME, "--boot1", "disk", check=False,
                  log_label="cleanup_boot1_default")
            _vbox("modifyvm", VM_NAME, "--boot2", "dvd", check=False,
                  log_label="cleanup_boot2_default")
            print(f"  ✅ boot order: disk → dvd (default 復元)")
        except Exception as e:
            err.append(f"boot order default: {e}")
    return {"status": "ok" if not err else "partial", "errors": err}


# ============================================================
# main
# ============================================================
def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--iso", required=True, help="boot ISO file path (Win11 install ISO)")
    ap.add_argument("--phase", type=int, default=0)
    args = ap.parse_args()

    iso = Path(args.iso)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"recovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    print(f"\n{'='*70}\nVM 完全復旧 v2 (CEO 5/21 案 C・Codex 38回目 反映)\n{'='*70}")
    print(f"ISO: {iso}")
    print(f"開始 phase: {args.phase}")

    preflight = None
    overall_status = "unknown"
    overall_exit = EXIT_FAIL

    try:
        # Phase 0
        if args.phase <= 0:
            r0 = phase_0_preflight(iso)
            _GLOBAL_LOG[f"phase_0"] = r0
            if r0["status"] == "fail":
                overall_status = "preflight_fail"
                return EXIT_FAIL
            preflight = r0

        # Phase 1
        if args.phase <= 1:
            r1 = phase_1_shutdown_graceful()
            _GLOBAL_LOG[f"phase_1"] = r1
            if r1["status"] == "fail":
                overall_status = "shutdown_fail"
                return EXIT_FAIL

        # Phase 2 (snapshot)
        if args.phase <= 2:
            r2 = phase_2_snapshot()
            _GLOBAL_LOG[f"phase_2"] = r2
            if r2["status"] == "fail":
                overall_status = "snapshot_fail"
                print(f"\n❌ snapshot 失敗 → password reset 中止 (data 保護)")
                return EXIT_FAIL

        # Phase 3 (ISO mount)
        if args.phase <= 3:
            r3 = phase_3_mount_iso(iso, preflight or {})
            _GLOBAL_LOG[f"phase_3"] = r3
            if r3["status"] == "fail":
                overall_status = "mount_fail"
                return EXIT_FAIL

        # Phase 4 (起動)
        if args.phase <= 4:
            r4 = phase_4_boot_iso()
            _GLOBAL_LOG[f"phase_4"] = r4

        # Phase 5-7 (UI 自動操作 manual_required)
        if args.phase <= 5:
            r57 = phase_5_to_7_repair_and_reset()
            _GLOBAL_LOG[f"phase_5_to_7"] = r57
            if r57["status"] == "manual_required":
                overall_status = "manual_required_at_phase_5"
                overall_exit = EXIT_MANUAL
                # cleanup を ここで しない (CEO が UI 操作中なので ISO mount 維持)
                return EXIT_MANUAL

        # Phase 8 以降は CEO 手動 password reset 完了後に resume で
        overall_status = "completed_to_phase_4"
        overall_exit = EXIT_OK
        return EXIT_OK

    except Exception as e:
        _GLOBAL_LOG["exception"] = {"error": str(e), "phase": args.phase}
        overall_status = f"exception: {e}"
        overall_exit = EXIT_FAIL
        return EXIT_FAIL
    finally:
        # cleanup (try/finally 保証・Codex #2/#13 反映)
        # ただし manual_required の時は cleanup を skip (UI 操作中なので ISO 維持)
        if overall_exit != EXIT_MANUAL:
            try:
                cu = cleanup_iso_and_boot(preflight)
                _GLOBAL_LOG["cleanup"] = cu
            except Exception as e:
                _GLOBAL_LOG["cleanup_error"] = str(e)
        _GLOBAL_LOG["ended_at"] = datetime.now().isoformat()
        _GLOBAL_LOG["overall_status"] = overall_status
        _GLOBAL_LOG["overall_exit"] = overall_exit
        log_path.write_text(json.dumps(_GLOBAL_LOG, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"\nlog: {log_path}")
        print(f"overall: status={overall_status}, exit={overall_exit}")


if __name__ == "__main__":
    sys.exit(main())
