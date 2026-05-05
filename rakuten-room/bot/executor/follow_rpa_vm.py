#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
楽天ROOM フォロー自動化 VM版（完全自動）

Phase 1: --scrape  ランキングからシードユーザー収集（5ジャンル×50人）
Phase 2: (default) シードユーザーのフォロワーリストからフォロー実行

使い方:
  python follow_rpa_v1.py --scrape              # シードユーザー収集
  python follow_rpa_v1.py --limit 90             # フォロー実行
  python follow_rpa_v1.py --limit 5 --dry-run    # クリックせず検出のみ
  python follow_rpa_v1.py --stop                  # 停止フラグ作成
"""
from __future__ import annotations
import argparse, io, json, logging, os, sys, time, random
from datetime import datetime
from pathlib import Path
import numpy as np
import pyperclip

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

import pyautogui
pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.3


def _minimize_console_window():
    """2026-04-22 07:30 patrol fix (CEO autonomous authorization):
    Minimize this python process's console (cmd.exe) window at startup so
    Ctrl+L / Ctrl+V / Enter keystrokes sent by pyautogui can NOT leak into cmd.
    With cmd minimized, only Chrome (or other remaining visible windows)
    can receive focus, eliminating the cmd-foreground bug that caused
    05:00 / 06:00 / 07:00 scheduler runs to record success=0.
    """
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            # SW_MINIMIZE = 6
            user32.ShowWindow(hwnd, 6)
            return True
    except Exception:
        pass
    return False


# IMPORTANT: call immediately so all subsequent pyautogui keystrokes target Chrome
_minimize_console_window()

BOT_DIR = Path(__file__).resolve().parent
STATE_PATH = BOT_DIR / "follow_rpa_state.json"
STOP_FLAG_PATH = BOT_DIR / "stop_flag.json"
LOG_PATH = BOT_DIR / "follow_rpa_log.json"
SEED_USERS_PATH = BOT_DIR / "seed_users.json"
EVIDENCE_DIR = BOT_DIR / "evidence"  # fail証跡保存先
MAX_EVIDENCE_CAPTURES = 20  # 最初の20件のfailで証跡保存

# --- Coordinates: auto-scaled ---

def _scale():
    """Calculate coordinate scale factors based on current screen size"""
    w, h = pyautogui.size()
    return w / BASE_W, h / BASE_H

def _sx(x):
    """Scale x coordinate"""
    return int(x * pyautogui.size()[0] / BASE_W)

def _sy(y):
    """Scale y coordinate"""
    return int(y * pyautogui.size()[1] / BASE_H)

# Base values (calibrated at 1920x909, dark mode)
_BTN_X = 1195
_SAFE_Y_MIN = 200
_SAFE_Y_MAX = 850
_FOLLOWER_LINK_X = 213
_FOLLOWER_LINK_Y = 628
_SCROLL_X = 700
_SCROLL_Y = 450
BASE_W, BASE_H = 1920, 909
SCROLL_AMOUNT = -400
Y_SCAN_STEP = 3
BTN_JUMP = 150

# --- limits ---
SESSION_MIN = 90
SESSION_MAX = 101
# 2026-05-03 バグ修正: ⑧修正で447seeds→1セッション2時間超になった問題を修正
# SESSION_SEED_BUDGET: 1セッションで走査するseed数の上限
# 199=旧設計と同等。ladies_fashion優先(⑧)は維持しつつ高速化。
# 残りseedは次セッションで継続(seed_progress経由)。
SESSION_SEED_BUDGET = 200
CLICK_COOLDOWN = 1.5
CLICK_COOLDOWN_MAX = 3.0
DUPLICATE_THRESHOLD = 20
MAX_CONSECUTIVE_SKIP = 30
# ⑥ APS早期次seed切替: 最低サンプル数を超えたら APS > 閾値で即次seedへ
APS_MIN_SAMPLE = 10       # 判定を始める最小試行数（skip除く）
APS_BAIL_THRESHOLD = 15.0 # attempts/success > この値で次seedへ

# --- color detection (light mode at 1920x909) ---
PINK_R_MIN = 200
PINK_G_MAX = 170

# --- genres for SCRAPE (5 genres) ---
GENRES = [
    ("sweets",    "https://room.rakuten.co.jp/discover/collectItemRank/2800000551167181"),
    ("kids",      "https://room.rakuten.co.jp/discover/collectItemRank/2800000100533426"),
    ("household", "https://room.rakuten.co.jp/discover/collectItemRank/2800000215783421"),
    ("kitchen",   "https://room.rakuten.co.jp/discover/collectItemRank/2800000558944399"),
    ("bags",      "https://room.rakuten.co.jp/discover/collectItemRank/2800000216131238"),
]

# --- genres for FOLLOW iteration (2026-05-02 改善方法⑧: ladies_fashion優先) ---
# H14確定: ladies_fashion=weak_count=3(最高効率), kids=weak_count=31(最低効率)
# 旧設計はGENRES (5ジャンル/199seed)のみ走査 → seed_users.jsonの248件が未使用だった
# 新設計: seed_users.json内の全11ジャンル(447seed)を効率順で走査
FOLLOW_GENRE_PRIORITY = [
    "ladies_fashion",  # H14: 最高効率 (weak_count=3)
    "interior",        # 未走査だった (37 seeds)
    "sweets",          # 既存 (37 seeds)
    "household",       # 既存 (39 seeds)
    "kitchen",         # 既存 (39 seeds)
    "bags",            # 既存 (39 seeds)
    "shoes",           # 未走査だった (40 seeds)
    "mens_fashion",    # 未走査だった (41 seeds)
    "food",            # 未走査だった (43 seeds)
    "all",             # 未走査だった (44 seeds)
    "kids",            # H14: 最低効率 (weak_count=31)
]

# 2026-04-22 08:30 pythonw化対応: pythonw.exe で起動する場合 stdout/stderr が
# 存在しないので StreamHandler だけだと何も残らない。FileHandler を必ず付与し、
# follow_rpa_text.log に全ログを書き出す（従来の JSON log とは別に可読テキスト）。
_LOG_FILE = BOT_DIR / "follow_rpa_text.log"
_handlers = [logging.FileHandler(_LOG_FILE, encoding="utf-8")]
try:
    if sys.stdout and hasattr(sys.stdout, "write"):
        _handlers.append(logging.StreamHandler(sys.stdout))
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", handlers=_handlers, force=True)
logger = logging.getLogger("follow_rpa")


# ==================== Utility ====================

def should_stop():
    return STOP_FLAG_PATH.exists()

def create_stop_flag():
    STOP_FLAG_PATH.write_text(json.dumps({"created_at": datetime.now().isoformat()}), encoding="utf-8")

def clear_stop_flag():
    if STOP_FLAG_PATH.exists():
        STOP_FLAG_PATH.unlink()

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"scroll_count":0,"total_success":0,"total_fail":0,"last_success_at":"","stop_reason":"","seed_progress":{"genre_index":0,"user_index":0}}

def save_state(state):
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)

def append_log(entry):
    logs = []
    if LOG_PATH.exists():
        try: logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        except: pass
    logs.append(entry)
    if len(logs) > 1000: logs = logs[-1000:]
    LOG_PATH.write_text(json.dumps(logs, ensure_ascii=False, indent=2), encoding="utf-8")
    # Copy to shared folder for host PC to read (multi-method fallback)
    content = json.dumps(logs, ensure_ascii=False, indent=2)
    _sync_log_to_share(content)


def _sync_log_to_share(content: str):
    """Sync log to HOST share with multiple fallback methods. 2026-05-04 fix."""
    import subprocess as _sp
    share_path = r"\\VBOXSVR\share\follow_rpa_log.json"

    # Method 1: Direct Python UNC write
    try:
        Path(share_path).write_text(content, encoding="utf-8")
        return  # success
    except Exception as e:
        logger.debug(f"[share_sync] direct write failed: {e}")

    # Method 2: net use reconnect + retry
    try:
        _sp.run(["net", "use", r"\\VBOXSVR\share"], capture_output=True, timeout=5)
        Path(share_path).write_text(content, encoding="utf-8")
        logger.info("[share_sync] recovered via net use reconnect")
        return
    except Exception as e:
        logger.debug(f"[share_sync] net-use+retry failed: {e}")

    # Method 3: CMD copy command
    try:
        src = str(LOG_PATH)
        result = _sp.run(
            ["cmd", "/c", f'copy "{src}" "{share_path}" /Y'],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            logger.info("[share_sync] recovered via cmd copy")
            return
        logger.debug(f"[share_sync] cmd copy rc={result.returncode} err={result.stderr[:100]}")
    except Exception as e:
        logger.debug(f"[share_sync] cmd copy exception: {e}")

    # Method 4: Z: drive (auto-mount alias for share)
    try:
        z = Path(r"Z:\follow_rpa_log.json")
        if z.parent.exists():
            z.write_text(content, encoding="utf-8")
            logger.info("[share_sync] recovered via Z: drive")
            return
    except Exception as e:
        logger.debug(f"[share_sync] Z: write failed: {e}")

    logger.warning("[share_sync] ALL 4 methods failed — log NOT synced to HOST")


# ==================== Fail Stats (E'' 計測基盤) ====================

class FailStats:
    """fail_reason別の計測。Phase1必須: 失敗を正しく測れる状態にする"""
    # E. verify_point_invalidを5分類に再分解 + 他分類
    REASONS = [
        "modal_open_failed",
        "no_button_detected",
        "click_intercepted",              # クリックがダイアログ等に遮られた
        "bbox_misaligned",                # ボタンbboxとクリック座標のズレ
        "verify_sample_on_white_area",    # verify判定点がボタン外の白領域
        "verify_sample_out_of_button",    # verify判定点がボタン外の白領域
        "button_state_changed_but_verify_missed",  # 実はピンクだがverify点がズレ
        "ui_delay_before_verify",         # UI描画遅延でverify時点では未変化
        "bbox_center_wrong",              # bbox中心計算が不正
        "already_followed_missed",        # 事前skipされず既フォロー済みをクリック
        "row_alignment_mismatch",         # 行検出位置と実際のボタン行がズレ
        "already_followed",               # 事前skip（正常）
        "unexpected_navigation",
        "rate_limit_detected",
        "ui_mismatch",
        "budget_exhausted",
        # 2026-04-22 マーケ指示 Phase 2: 前提チェック 4 種
        "foreground_mismatch",            # Chrome がフォアグラウンドでない（cmd/File Explorer に keystroke 流入）
        "page_signature_mismatch",        # URL bar / DOM 署名が期待ページと不一致
        "visible_but_unbound",            # ピンクボタン可視だがクリックしても bind 先なし
        "input_target_uncertain",         # 入力ターゲットが確定できない（precondition abort）
    ]

    def __init__(self):
        self.counts = {r: 0 for r in self.REASONS}
        self.total_attempts = 0
        self.success = 0
        self.fail_actionable = 0  # 修正可能なfail（already_followed除く）
        self.weak_seeds = []
        self.evidence_count = 0
        self._init_evidence_dir()

    def _init_evidence_dir(self):
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.evidence_path = EVIDENCE_DIR / session_id
        self.evidence_path.mkdir(parents=True, exist_ok=True)

    def record(self, reason):
        if reason in self.counts:
            self.counts[reason] += 1
        if reason not in ("already_followed", "budget_exhausted"):
            self.fail_actionable += 1

    def record_success(self):
        self.success += 1
        self.total_attempts += 1

    def record_attempt(self):
        self.total_attempts += 1

    def add_weak_seed(self, seed_user):
        if seed_user not in self.weak_seeds:
            self.weak_seeds.append(seed_user)

    @staticmethod
    def _normalize_for_json(obj):
        """numpy/bool/tuple等をJSON直列化可能な素のPython型に正規化"""
        if isinstance(obj, dict):
            return {k: FailStats._normalize_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [FailStats._normalize_for_json(v) for v in obj]
        elif isinstance(obj, (bool,)):
            return int(obj)
        elif hasattr(obj, 'item'):  # numpy scalar (np.int64, np.float64, np.bool_)
            return obj.item()
        elif isinstance(obj, float):
            return round(obj, 4)
        elif isinstance(obj, Path):
            return str(obj)
        return obj

    def save_evidence(self, ss_before, ss_after, click_x, click_y, btn_region, after_r, after_g, seed_user, scroll_idx, row_idx):
        """fail証跡を保存（最初のMAX_EVIDENCE_CAPTURES件のみ）"""
        if self.evidence_count >= MAX_EVIDENCE_CAPTURES:
            return
        self.evidence_count += 1
        prefix = f"fail_{self.evidence_count:03d}"
        try:
            if ss_before:
                ss_before.save(str(self.evidence_path / f"{prefix}_before.png"))
            if ss_after:
                ss_after.save(str(self.evidence_path / f"{prefix}_after.png"))
            meta = self._normalize_for_json({
                "click_x": click_x, "click_y": click_y,
                "btn_region": btn_region,
                "after_rgb": [after_r, after_g],
                "seed_user": seed_user,
                "scroll_idx": scroll_idx,
                "row_idx": row_idx,
                "timestamp": datetime.now().isoformat(),
            })
            (self.evidence_path / f"{prefix}_meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"  Evidence save failed: {e}")

    @property
    def attempts_per_success(self):
        return round(self.total_attempts / self.success, 2) if self.success > 0 else float('inf')

    @property
    def most_frequent_reason(self):
        if not any(self.counts.values()):
            return "none"
        return max(self.counts, key=self.counts.get)

    def summary_dict(self):
        return {
            "total_attempts": self.total_attempts,
            "success": self.success,
            "fail_total": sum(self.counts.values()),
            "fail_actionable": self.fail_actionable,
            "skip_total": self.counts.get("already_followed", 0),
            "attempts_per_success": self.attempts_per_success,
            "most_frequent_stop_reason": self.most_frequent_reason,
            "weak_seed_count": len(self.weak_seeds),
            "weak_seeds": self.weak_seeds[:10],
            "fail_breakdown": dict(self.counts),
            "evidence_captured": self.evidence_count,
        }

    def log_summary(self):
        s = self.summary_dict()
        logger.info(f"\n{'='*50}")
        logger.info(f"  E'' Metrics:")
        logger.info(f"    total_attempts: {s['total_attempts']}")
        logger.info(f"    success: {s['success']}")
        logger.info(f"    fail_total: {s['fail_total']}")
        logger.info(f"    attempts_per_success: {s['attempts_per_success']}")
        logger.info(f"    most_frequent_reason: {s['most_frequent_stop_reason']}")
        logger.info(f"    weak_seed_count: {s['weak_seed_count']}")
        for reason, count in self.counts.items():
            if count > 0:
                logger.info(f"    {reason}: {count}")
        logger.info(f"{'='*50}")


# ==================== Chrome Management ====================

import subprocess

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CHROME_PROFILE = os.path.expanduser(r"~\AppData\Local\Google\Chrome\User Data")

def _clear_chrome_cache_if_needed() -> None:
    """Chrome起動前にキャッシュを無条件クリア — ストレージ容量警告防止。

    2026-05-03 Iter30 実装: Chrome累積キャッシュ(403K+ファイル)が原因で
    Windowsストレージ警告ダイアログ(title='設定')が表示され、
    foreground_mismatch→bot全停止→14.1h dead zoneが発生した根本対策。

    - ログイン状態(Cookies/Login Data)はキャッシュ外なので影響なし。
    - HTTPレスポンスキャッシュのみ削除。再起動時に自動再構築される。
    - Chrome未起動状態でのみ呼び出すこと（launch_chrome()冒頭で呼ぶ）。
    """
    import shutil as _shutil
    cache_dirs = [
        Path(CHROME_PROFILE) / "Default" / "Cache",
        Path(CHROME_PROFILE) / "Default" / "Code Cache",
    ]
    for cache_dir in cache_dirs:
        if not cache_dir.exists():
            continue
        try:
            _shutil.rmtree(cache_dir, ignore_errors=True)
            cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"[CACHE] {cache_dir.name} cleared (storage警告防止)")
        except Exception as _e:
            logger.warning(f"[CACHE] clear failed ({cache_dir.name}): {_e}")


def launch_chrome():
    """Launch Chrome, fullscreen, bring to foreground"""
    # ストレージ容量警告防止: 起動前にキャッシュをクリア (2026-05-03 Iter30)
    _clear_chrome_cache_if_needed()
    logger.info("  Launching Chrome...")
    subprocess.Popen([
        CHROME_PATH,
        f"--user-data-dir={CHROME_PROFILE}",
        "--start-maximized",
        "https://room.rakuten.co.jp/"
    ])
    time.sleep(10)
    # Maximize Chrome window
    pyautogui.hotkey('win', 'up')
    time.sleep(1)
    ss = pyautogui.screenshot()
    logger.info(f"  Chrome launched (screen: {ss.size})")

def close_chrome():
    """Close all Chrome windows gracefully, then force kill if needed"""
    logger.info("  Closing Chrome...")
    # Try graceful close first
    try:
        subprocess.run(["taskkill", "/IM", "chrome.exe"], capture_output=True, timeout=5)
    except:
        pass
    time.sleep(2)
    # Force kill if still running
    try:
        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe"], capture_output=True, timeout=5)
    except:
        pass
    time.sleep(1)
    logger.info("  Chrome closed")


# ==================== Navigation ====================

def _focus_chrome():
    """Bring Chrome window to foreground before sending keystrokes.

    2026-04-22 07:30 patrol fix (CEO autonomous authorization):
      - cmd-foreground bug: pyautogui が送信する Ctrl+L / Ctrl+V / Enter が
        cmd窓にフォーカスが残ったまま入力され Chrome に届かず
        cmd に 'Lhttps://...' が type されて 'is not recognized as internal command' 連発
      - 07:00 scheduler run が attempts=2 success=0 で即脱落・夜間累計でも頻発
      - 恒久対策: navigate 毎回 Chrome window を activate してから keystroke を送る。
      - 第一候補 pygetwindow、失敗時は ctypes SetForegroundWindow を fallback。
    """
    # Try 1: pygetwindow (pyautogui に同梱される Windows デフォルト依存)
    try:
        import pygetwindow as gw
        candidates = [w for w in gw.getAllWindows()
                      if 'Chrome' in (getattr(w, 'title', '') or '') and w.visible]
        if candidates:
            w = candidates[0]
            try:
                if getattr(w, 'isMinimized', False):
                    w.restore()
            except Exception:
                pass
            try:
                w.activate()
                time.sleep(0.25)
                return True
            except Exception:
                pass
    except Exception:
        pass
    # Try 2: ctypes Win32 API (pygetwindow が使えない/失敗時)
    try:
        import ctypes
        user32 = ctypes.windll.user32
        found = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if 'Chrome' in buf.value:
                found.append(hwnd)
            return True
        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        if found:
            hwnd = found[0]
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.25)
            return True
    except Exception:
        pass
    return False


# ==================== 2026-04-22 Phase 2: 前提チェック（マーケ指示）====================

def check_foreground_is_chrome():
    """Chrome がフォアグラウンドか確認。
    戻り値: (is_chrome_bool, detail_str)
    - cmd-foreground bug / File Explorer 奪取 の両方を検出できる。
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False, "no_foreground_window"
        # クラス名取得
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value
        # ウィンドウタイトル取得
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1) if length > 0 else ctypes.create_unicode_buffer(1)
        if length > 0:
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value
        # Chrome class: "Chrome_WidgetWin_1"
        is_chrome = (cls == "Chrome_WidgetWin_1") or ("Chrome" in title and "Google Chrome" in title)
        detail = f"cls={cls!r} title={title!r}"
        return is_chrome, detail
    except Exception as e:
        return False, f"error:{e}"


_DISMISSABLE_TITLES = {'設定', 'Windows の設定', 'ストレージ', 'Microsoft Store', 'ディスク領域不足', 'Disk Space Low'}
_DISMISSABLE_CLASSES = {'ApplicationFrameWindow', 'Windows.UI.Core.CoreWindow'}

def _try_dismiss_system_dialog() -> bool:
    """フォアグラウンドにWindowsシステムダイアログが出ている場合Alt+F4で閉じる。

    2026-05-03 Iter30: Chromeキャッシュ満杯時に title='設定'(ストレージ設定)が
    前景に出て foreground_mismatch→14.1h dead zone 発生。
    _clear_chrome_cache_if_needed()で根本対策済みだが、万一出た場合の保険。
    戻り値: True=ダイアログを閉じた / False=対象なし
    """
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        cls = cls_buf.value
        length = user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1) if length > 0 else ctypes.create_unicode_buffer(1)
        if length > 0:
            user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value
        if cls in _DISMISSABLE_CLASSES or title in _DISMISSABLE_TITLES:
            logger.warning(f"[DISMISS] システムダイアログ検知: cls={cls!r} title={title!r} → Alt+F4")
            pyautogui.hotkey('alt', 'f4')
            time.sleep(1.0)
            return True
    except Exception as _e:
        logger.warning(f"[DISMISS] 検知失敗: {_e}")
    return False


def ensure_chrome_foreground(max_attempts=3):
    """Chrome foreground を最大 max_attempts 回試行。成功/失敗/詳細を返す。
    失敗時は stop_reason=foreground_mismatch 推奨。

    2026-05-03 Iter30: Windowsシステムダイアログ(設定/ストレージ)が
    前景に出た場合にAlt+F4で閉じてからChrome再フォーカスを試みる。
    """
    for i in range(max_attempts):
        _minimize_console_window()
        _focus_chrome()
        time.sleep(0.3)
        ok, detail = check_foreground_is_chrome()
        if ok:
            return True, f"attempt={i+1} {detail}"
        # システムダイアログが前景なら閉じてリトライ
        _try_dismiss_system_dialog()
    return False, f"max_attempts_exceeded last={detail}"


def check_page_signature(expected_url_fragment):
    """現在の URL bar の内容を読み取って expected_url_fragment を含むか確認。
    Ctrl+L → Ctrl+C でクリップボード経由で URL を取得。
    戻り値: (matches_bool, actual_url_str)
    """
    try:
        import pyautogui
        # foreground が Chrome でなければ信頼できない
        ok, detail = check_foreground_is_chrome()
        if not ok:
            return False, f"not_chrome_fg:{detail}"
        # 現在のクリップボードを退避
        try:
            saved = pyperclip.paste()
        except Exception:
            saved = None
        # URL bar フォーカス + 全選択 + コピー
        pyautogui.hotkey('ctrl', 'l')
        time.sleep(0.3)
        pyautogui.hotkey('ctrl', 'a')
        time.sleep(0.2)
        pyautogui.hotkey('ctrl', 'c')
        time.sleep(0.4)
        try:
            actual = pyperclip.paste() or ""
        except Exception:
            actual = ""
        # クリップボード復元（副作用最小化）
        if saved is not None:
            try:
                pyperclip.copy(saved)
            except Exception:
                pass
        matches = expected_url_fragment in actual
        return matches, actual
    except Exception as e:
        return False, f"error:{e}"


# ============================================================
# 2026-05-05 Phase 2-1: ログイン状態検知
# ============================================================

def check_login_status() -> tuple[bool, str]:
    """楽天ROOMログイン状態を判定。
    /feed（ログイン必須URL）にnavigate → リダイレクト先URLを判定。

    Returns: (is_logged_in: bool, detail: str)
    """
    try:
        # /feed はログイン状態で /feed/items にリダイレクト、未ログインで login pageへ
        navigate_to_url("https://room.rakuten.co.jp/feed")
        time.sleep(3)
        # URL bar 直接読取（check_page_signature と同じ機構）
        ok, actual = check_page_signature("/feed")
        if not ok:
            actual = actual if isinstance(actual, str) else str(actual)
        # ログイン済み判定: /feed/items または /feed/ にいる
        if "room.rakuten.co.jp/feed" in actual:
            return True, f"logged_in (url={actual[:120]})"
        # ログアウト判定: 楽天IDログインページにリダイレクト
        if any(k in actual.lower() for k in ["login.rakuten.co.jp", "/nid/", "rms.rakuten", "myinfo"]):
            return False, f"redirected_to_login (url={actual[:120]})"
        # 不明
        return False, f"unexpected_url (url={actual[:120]})"
    except Exception as e:
        return False, f"check_error: {e}"


# ============================================================
# 2026-05-05 Phase 2-2: heartbeat 同期 (VM→HOST)
# ============================================================

HEARTBEAT_PATH_LOCAL = BOT_DIR / "follow_heartbeat.json"
HEARTBEAT_PATH_SHARE = Path(r"\\VBOXSVR\share\follow_heartbeat.json")
_HEARTBEAT_LAST_WRITE = 0.0
_HEARTBEAT_INTERVAL = 30.0  # 30秒以上経過していれば書込む（連打防止）


def write_heartbeat(phase: str, current_seed: str = "", success: int = 0, fail: int = 0, extra: dict = None, force: bool = False):
    """heartbeat を local + shared folder に atomic write。

    phase: 'startup' | 'navigate' | 'detect' | 'click' | 'verify' | 'cooldown' | 'shutdown'
    force=True なら interval を無視
    """
    global _HEARTBEAT_LAST_WRITE
    now = time.time()
    if not force and (now - _HEARTBEAT_LAST_WRITE) < _HEARTBEAT_INTERVAL:
        return
    _HEARTBEAT_LAST_WRITE = now
    data = {
        "ts": datetime.now().isoformat(),
        "pid": os.getpid(),
        "phase": phase,
        "current_seed": current_seed,
        "success_count": success,
        "fail_count": fail,
        "extra": extra or {},
    }
    content = json.dumps(data, ensure_ascii=False)
    for p in [HEARTBEAT_PATH_LOCAL, HEARTBEAT_PATH_SHARE]:
        try:
            tmp = p.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8")
            tmp.replace(p)
        except Exception as e:
            # share 書込失敗は debug only（patrol が age で検知）
            logger.debug(f"[heartbeat] {p} write failed: {e}")


def preflight_check(expected_url_fragment=None):
    """navigate/click 実行前の 5 点前提チェック。
    戻り値: (ok_bool, stop_reason_str_or_None, detail_str)

    チェック項目:
      1. Chrome foreground （cmd/File Explorer 奪取検知）
      2. URL bar 内容が expected_url_fragment を含む（page_signature_mismatch 検知）
      3. （click時のみ）直前の screenshot がフォロワーページ署名に合致
      4. 入力ターゲット整合（foreground と keystroke 到達先の一致）
      5. 入力確定性（全チェック通過時のみ click/navigate を許可）

    expected_url_fragment=None の場合は URL check をスキップ（初回 navigate 前など）。
    """
    # 1, 4, 5: foreground check
    ok_fg, fg_detail = ensure_chrome_foreground(max_attempts=3)
    if not ok_fg:
        return False, "foreground_mismatch", fg_detail

    # 2, 3: URL/page signature check（指定ある時のみ）
    if expected_url_fragment:
        ok_url, actual = check_page_signature(expected_url_fragment)
        if not ok_url:
            return False, "page_signature_mismatch", f"expected={expected_url_fragment!r} actual={actual!r}"

    return True, None, f"fg_ok url_ok={expected_url_fragment!r}"


def navigate_to_url(url):
    """Ctrl+L -> paste URL -> Enter

    2026-04-22 02:30 patrol fix:
      - Open File dialog (Chrome Ctrl+O modal) が稀に開いて navigate 完全停止する事象対策
      - navigate の最初に Escape を2回送って modal を dismiss

    2026-04-22 07:30 patrol fix (CEO autonomous authorization):
      - cmd-foreground bug 恒久対策: 各 navigate 前に Chrome window を activate
      - これで Ctrl+L / Ctrl+V / Enter が Chrome に届くことを担保する

    2026-04-22 08:10 patrol fix (CEO autonomous authorization):
      - 07:35 recovery run (success=2, attempts=39, stop=no_button_detected) で
        minimize at main 1回だけでは不十分・途中から cmd が再表示→keystroke leak
      - navigate 毎回 cmd console を minimize してから Chrome activate する
        （import 時 + 各 navigate 直前の 2段構え）
    """
    # CRITICAL: re-minimize cmd console each navigate to prevent keystroke leak
    _minimize_console_window()
    # CRITICAL: bring Chrome to foreground so keystrokes go to Chrome not cmd
    _focus_chrome()
    # Defensive: dismiss any stuck modal (Open File dialog, restore bubble, alert etc.)
    pyautogui.press('escape')
    time.sleep(0.2)
    pyautogui.press('escape')
    time.sleep(0.3)
    pyautogui.hotkey('ctrl', 'l')
    time.sleep(0.5)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.2)
    pyperclip.copy(url)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.3)
    pyautogui.press('enter')

def open_follower_modal():
    """Wait for followers page load, scan for follow buttons. Returns True if buttons detected.
    2026-04-21 #281 修正: legacy 単列(btn_x=1195 ±30)から WIDE scan へ切替。
      実ボタン click_x≈1305 は legacy レンジ外で取りこぼしていた。
      scan_only_loop / detect_buttons と同じ方式で右半分を 80px 刻みに走査する。
    """
    time.sleep(5)
    pyautogui.press('home')
    time.sleep(1)

    ss = pyautogui.screenshot()
    arr = np.array(ss)
    h, w = arr.shape[:2]

    # WIDE scan: 右半分 (55%~95%) を 80px 刻みで走査
    seen_y = set()
    detected = []
    for col_x in range(int(w * 0.55), int(w * 0.95), 80):
        col_detected = _scan_buttons_at(arr, col_x)
        for b in col_detected:
            y = b["y"]
            if any(abs(y - sy) < 40 for sy in seen_y):
                continue
            seen_y.add(y)
            detected.append(b)

    # false-positive filter (scan_only_loop と同じ条件)
    y_cap = int(h * 0.93)
    click_x_cap = int(w * 0.75)
    real = [
        b for b in detected
        if b["y"] < y_cap
        and (b.get("click_x") is None or b["click_x"] < click_x_cap)
    ]

    if real:
        logger.info(f"  Followers page loaded (wide scan: {len(real)} buttons)")
        return True
    else:
        logger.warning(f"  No buttons detected on followers page (wide scan, raw={len(detected)})")
        return False

def navigate_to_seed_user(seed_user):
    """Navigate directly to seed user's followers page. Retry on failure.

    2026-04-22 Phase 2: 前提チェック統合
      - navigate 前に Chrome foreground 確認（foreground_mismatch 検知）
      - navigate 後に URL bar 内容確認（page_signature_mismatch 検知）
      - 両チェック失敗なら stop_reason を返して呼び出し側で即 safe_stop
    戻り値: (ok_bool, stop_reason_str_or_None)
    """
    # Direct URL navigation to /followers — no click required (bypass hardcoded coord)
    url = f"https://room.rakuten.co.jp/{seed_user}/followers"
    expected_fragment = f"/{seed_user}/followers"
    for attempt in range(3):
        logger.info(f"  -> {seed_user}/followers" + (f" (retry {attempt})" if attempt > 0 else ""))

        # Pre-navigate: Chrome foreground 確認（cmd-fg bug / File Explorer 奪取検知）
        ok_pre, reason_pre, detail_pre = preflight_check(expected_url_fragment=None)
        if not ok_pre:
            logger.error(f"  preflight FAILED before navigate: {reason_pre} {detail_pre}")
            # 1回目は保険として再 focus してから navigate 強行、2回目以降は abort
            if attempt == 0:
                logger.warning("  retry with re-focus")
                continue
            return False, reason_pre

        navigate_to_url(url)
        time.sleep(2.5)  # 楽天 SPA ロード待ち

        # Post-navigate: URL bar 内容確認（wrong page 到達 / modal 発火検知）
        ok_url, actual_url = check_page_signature(expected_fragment)
        if not ok_url:
            logger.warning(f"  URL mismatch: expected={expected_fragment!r} actual={actual_url[:120]!r}")
            continue

        if open_follower_modal():
            return True, None
        logger.warning(f"  Followers page didn't render buttons - retrying")
    logger.error(f"  Could not load followers for {seed_user} - skipping")
    return False, "page_signature_mismatch"


# ==================== Scrape ====================

def run_console_js(js_code, wait_seconds=20):
    """Paste and run JS in an already-open DevTools Console. Returns clipboard content."""
    pyperclip.copy(js_code)
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.5)
    pyautogui.press('enter')
    time.sleep(wait_seconds)
    return pyperclip.paste()

def scrape_seed_users():
    """Scrape ranking pages for seed users via DevTools Console JS."""
    all_seeds = {}

    logger.info("=== Seed User Scraping Start ===")
    logger.info(f"  Genres: {len(GENRES)}")
    # Auto-launch Chrome (2026-05-04): 既存Chromeを閉じてから新規起動
    logger.info("  Closing any existing Chrome instances...")
    close_chrome()  # 既存Chromeを確実に終了させる
    logger.info("  Auto-launching Chrome for scrape...")
    _clear_chrome_cache_if_needed()
    subprocess.Popen([
        CHROME_PATH,
        f"--user-data-dir={CHROME_PROFILE}",
        "--start-maximized",
        "https://room.rakuten.co.jp/",
    ])
    logger.info("  Waiting 15s for Chrome to load and come to foreground...")
    time.sleep(15)  # 15s: Chrome起動待機

    # Step 1: Open DevTools Console (keep open throughout entire scrape)
    logger.info("  Opening DevTools Console...")
    pyautogui.hotkey('ctrl', 'shift', 'j')
    time.sleep(2)

    # Step 2: Unlock paste (once per domain, stays while DevTools is open)
    logger.info("  Typing 'allow pasting'...")
    pyautogui.typewrite('allow pasting', interval=0.03)
    pyautogui.press('enter')
    time.sleep(1)

    for genre_key, genre_url in GENRES:
        logger.info(f"\n--- Scraping: {genre_key} ---")

        # Navigate via console (keeps DevTools open & paste unlocked)
        nav_js = f"window.location.href='{genre_url}';"
        pyperclip.copy(nav_js)
        pyautogui.hotkey('ctrl', 'v')
        time.sleep(0.3)
        pyautogui.press('enter')
        logger.info(f"  Navigating...")
        time.sleep(6)

        # 2026-05-04 修正: async→sync方式に変更。asyncのcopy()タイムアウト問題を解決
        # Step A: scroll to bottom synchronously (lazy load trigger)
        scroll_js = "window.scrollTo(0,99999);void 0;"
        run_console_js(scroll_js, wait_seconds=1)
        logger.info(f"  Waiting 5s for lazy content to load...")
        time.sleep(5)

        # Step B: extract synchronously (IIFE, no async/await, copy() runs immediately)
        # 2026-05-04: 広範パターンに変更 - /itemsだけでなく全user-likeパスを抽出
        # /discover/や既知非ユーザーパスを除外し、残りのパスをユーザー名として抽出
        _skip = "discover,feature,r,campaign,help,search,ranking,top,genre,my,cart,event,guide,news,info"
        js = (
            "(()=>{"
            "const skip=new Set('" + _skip + "'.split(','));"
            "const hrefs=[...document.querySelectorAll('a[href*=\\'room.rakuten.co.jp\\']')]"
            ".map(a=>a.href);"
            "const u=[...new Set(hrefs"
            ".map(h=>{const m=h.match(/room\\.rakuten\\.co\\.jp\\/([^/?#]+)/);return m?m[1]:null})"
            ".filter(x=>x&&x.length>2&&!skip.has(x)&&!x.includes('.')&&x!=='my'))]"
            ".slice(0,100);"
            f"copy(JSON.stringify(u));console.log('SYNC:{genre_key}:'+u.length+'/'+"
            "hrefs.length+'links')"
            "})();"
        )

        logger.info(f"  Running extraction JS (sync)...")
        result = run_console_js(js, wait_seconds=5)

        try:
            parsed = json.loads(result)
            filtered = [u for u in parsed if not u.startswith('discover') and u != 'my' and len(u) > 2
                        and not u.startswith('cdn.') and '.' not in u]  # 無効URL除去
            users = filtered[:100]  # 50→100 (2026-05-02 seed pool expansion)
            all_seeds[genre_key] = users
            logger.info(f"  {genre_key}: {len(users)} users")
            for i, u in enumerate(users[:5]):
                logger.info(f"    {i+1}. {u}")
            if len(users) > 5:
                logger.info(f"    ... +{len(users)-5} more")
        except Exception as e:
            logger.error(f"  {genre_key}: FAILED - {e}")
            logger.error(f"  clipboard: {result[:200]}")
            all_seeds[genre_key] = []

        # (DevTools stays open for next genre)

    # Close DevTools after all genres done
    pyautogui.hotkey('ctrl', 'shift', 'j')
    time.sleep(1)

    # Save results: 既存データとマージ（上書き禁止 — 全ジャンルが0件のスクレイプ失敗で既存データを消さない）
    existing = {}
    if SEED_USERS_PATH.exists():
        try:
            existing = json.loads(SEED_USERS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    # 新規スクレイプ結果が0件のジャンルは既存データを維持
    merged = dict(existing)
    for gk, users in all_seeds.items():
        if users:  # 0件スクレイプで既存を上書きしない
            merged[gk] = users
        elif gk not in merged:
            merged[gk] = []
    SEED_USERS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    total = sum(len(v) for v in merged.values())
    logger.info(f"\n{'='*50}")
    logger.info(f"Scrape Complete: {total} users across {len(merged)} genres (merged)")
    for k, v in merged.items():
        fresh = len(all_seeds.get(k, []))
        logger.info(f"  {k}: {len(v)} users ({fresh} fresh this scrape)")
    logger.info(f"Saved to: {SEED_USERS_PATH}")
    logger.info(f"{'='*50}")
    return merged


# ==================== Color Detection ====================

def get_region_color(ss, x, y, dx=30, dy=10):
    arr = np.array(ss)
    y1, y2 = max(0, y-dy), min(arr.shape[0], y+dy)
    x1, x2 = max(0, x-dx), min(arr.shape[1], x+dx)
    region = arr[y1:y2, x1:x2]
    return region[:,:,0].mean(), region[:,:,1].mean(), region[:,:,2].mean()

def is_pink(r, g, b=0):
    return r > PINK_R_MIN and g < PINK_G_MAX and (r - g) > 50


def verify_follow_success(ss_after, click_x, click_y, dx=30, dy=8):
    """
    多点サンプリングによるフォロー成功判定（verify再設計）

    2026-05-05 Phase A-3 修正: dx=50→30, dy=15→8 に縮小し、9点→5点に削減。
        旧設定では sampling 点が button bounds (typical width ~120px, height ~30px)
        の外に出てしまい、白領域 (white_area) や ボタン外 (out_of_button) と判定される
        失敗が58% (verify_sample_out_of_button 32% + verify_sample_on_white_area 25%)
        を占めていた。サンプリング集中度を上げて button 内部にしっかり当てる。

    1点でもピンクならTrue。全点白ならFalse。

    Returns: (is_success, pink_ratio, detail)
    """
    arr = np.array(ss_after)
    h, w = arr.shape[:2]

    # サンプリング点: button内部 5点 (中央 + 上下左右の半径dx/dy)
    # 旧 9 点(dx=50/dy=15)はboundsを外す問題があったため、5点(dx=30/dy=8)に削減
    sample_points = [
        (click_x, click_y),                 # 中央 (最重要)
        (click_x - dx//2, click_y),         # 左
        (click_x + dx//2, click_y),         # 右
        (click_x, click_y - dy//2),         # 上
        (click_x, click_y + dy//2),         # 下
    ]

    pink_count = 0
    total_valid = 0
    detail_points = []

    for px, py in sample_points:
        px = max(5, min(w-5, px))
        py = max(5, min(h-5, py))
        # 5x5領域の平均色
        region = arr[py-2:py+3, px-2:px+3]
        if region.size == 0:
            continue
        r, g = region[:,:,0].mean(), region[:,:,1].mean()
        total_valid += 1
        is_p = is_pink(r, g)
        if is_p:
            pink_count += 1
        detail_points.append({"x": int(px), "y": int(py), "r": round(float(r)), "g": round(float(g)), "pink": int(is_p)})

    pink_ratio = pink_count / total_valid if total_valid > 0 else 0
    is_success = pink_count > 0  # 1点でもピンクなら成功

    return is_success, pink_ratio, detail_points

def is_follow_button(r, g, b):
    return 200 < r < 255 and 200 < g < 255 and abs(r-g) < 20

def auto_find_btn_x(ss):
    """Scan the right side of screen to find the x-position of follow buttons.
    Scans right-to-left because follow buttons are anchored at the right edge,
    while user avatars/images on the left can produce false-positive color matches."""
    arr = np.array(ss)
    h, w = arr.shape[:2]
    # Scan from right (85%) back to 50% of screen width, in steps of 20px
    for x_center in range(int(w * 0.85), int(w * 0.50), -20):
        x1, x2 = x_center - 50, x_center + 50
        if x2 > w or x1 < 0: continue
        # Check multiple y positions for button-like colors
        for y in range(int(h * 0.25), int(h * 0.85), 50):
            if y + 30 > h: break
            region = arr[y:y+30, x1:x2]
            if region.size == 0: continue
            r, g = region[:,:,0].mean(), region[:,:,1].mean()
            std = region.std()
            # Pink (following) or White with text (follow button)
            if (r > PINK_R_MIN and g < PINK_G_MAX and (r-g) > 50) or \
               (r > 200 and g > 200 and abs(r-g) < 20 and std > 25):
                return x_center
    return None

# Cache for detected button x position
_detected_btn_x = None


def _compute_btn_centroid_x(arr, y_top, x1, x2, status):
    """Given a detected button row (band: y_top..y_top+30, x1..x2), find the horizontal
    centroid of button pixels. For 'following' (pink bg), use pink mask. For 'not_following'
    (white bg with pink 'フォロー' text), use combined white-panel + pink-text mask.

    Returns absolute centroid x (screen coord) or None if mask is empty.
    2026-04-21 #281 CEO指摘対応: 列固定だとボタン外クリックで verify fail 多発。
    per-button centroid で click 精度を改善する。
    """
    h, w = arr.shape[:2]
    y2 = min(h, y_top + 30)
    band = arr[y_top:y2, x1:x2]
    if band.size == 0:
        return None
    R = band[:, :, 0].astype(int)
    G = band[:, :, 1].astype(int)
    if status == "following":
        # pink bg: pink mask
        mask = (R > PINK_R_MIN) & (G < PINK_G_MAX) & ((R - G) > 50)
    else:
        # not_following: white panel (200-255 RGB, |r-g|<20) + pink text (フォロー文字)
        m_white = (R > 200) & (R < 255) & (G > 200) & (G < 255) & (abs(R - G) < 20)
        m_pink_text = (R > PINK_R_MIN) & (G < PINK_G_MAX) & ((R - G) > 50)
        mask = m_white | m_pink_text
    if not mask.any():
        return None
    # use pink text centroid preferentially if present (most accurate center of button)
    if status != "following":
        m_pink_text = (R > PINK_R_MIN) & (G < PINK_G_MAX) & ((R - G) > 50)
        if m_pink_text.any():
            ys, xs = np.where(m_pink_text)
            return int(xs.mean()) + x1
    ys, xs = np.where(mask)
    return int(xs.mean()) + x1


def _scan_buttons_at(arr, btn_x):
    """Scan for follow buttons at a specific btn_x column. Returns list of detected buttons.
    Each button dict carries its own per-button click_x (centroid of button pixels in row)."""
    h, w = arr.shape[:2]
    scan_x1 = max(0, btn_x - 70)
    scan_x2 = min(w, btn_x + 70)
    sub_x1 = max(0, btn_x - 50)
    sub_x2 = min(w, btn_x + 50)

    buttons = []
    y_min = int(h * 0.22)
    y_max = int(h * 0.93)
    step = 3
    jump = int(h * 0.16)

    y = y_min
    while y < y_max:
        if y + 30 > h:
            break
        region = arr[y:y+30, scan_x1:scan_x2]
        if region.size == 0:
            y += step; continue
        avg_r, avg_g, avg_b = region[:,:,0].mean(), region[:,:,1].mean(), region[:,:,2].mean()
        sub = arr[y:y+30, sub_x1:sub_x2]
        std = sub.std() if sub.size > 0 else 0
        # 2026-04-21 #281 CRITICAL FIX: 現行 Rakuten UI では「フォロー中」ボタンも白背景 + 小さな
        # 🎀 ピンクアイコンに変わっており、領域平均では is_pink がほぼ発火しない。
        # ボタン領域内の「reddish pixel count」で分類する。
        #   motoseal「フォロー中」: reddish=72 (R=220-224, G=144-163, B=144-163) → following
        #   kurage「フォロー中」:  reddish~30 (anti-aliased icon edges)            → following
        #   aniko「フォロー」:       reddish=0                                       → not_following
        R_region = region[:,:,0].astype(int)
        G_region = region[:,:,1].astype(int)
        # loose reddish: icon anti-alias 含む
        reddish_mask = (R_region > 150) & (G_region < 150) & ((R_region - G_region) > 30)
        reddish_count = int(reddish_mask.sum())
        # strict pink: 核となる icon 中心
        pink_mask = (R_region > 200) & (G_region < 170) & ((R_region - G_region) > 50)
        pink_count = int(pink_mask.sum())
        # following 判定: strict pink > 3 OR loose reddish > 15 OR 旧 avg が pink
        if pink_count > 3 or reddish_count > 15 or is_pink(avg_r, avg_g):
            cx = _compute_btn_centroid_x(arr, y, scan_x1, scan_x2, "following")
            buttons.append({"y": y+15, "status": "following", "r": avg_r, "g": avg_g,
                            "pink_count": pink_count,
                            "click_x": cx if cx is not None else btn_x})
            y += jump
        elif is_follow_button(avg_r, avg_g, avg_b) and std > 25:
            cx = _compute_btn_centroid_x(arr, y, scan_x1, scan_x2, "not_following")
            buttons.append({"y": y+15, "status": "not_following", "r": avg_r, "g": avg_g,
                            "pink_count": pink_count,
                            "click_x": cx if cx is not None else btn_x})
            y += jump
        else:
            y += step
    return buttons


def _apply_false_positive_filter(buttons, h, w):
    """2026-04-21 #281: Production filter mirroring scan_only_loop.
    - y >= int(h*0.93) → Windows taskbar / Chrome status bar kabri (実ボタン y=801 残し、y=847 除外)
    - click_x >= int(w*0.75) → 右端 scrollbar / thumbnail artifact (実 click_x≈1305-1325 残し、1441 除外)
    いずれも scan-only 5画面実測で deterministic に再現した誤検知パターン。
    """
    y_cap = int(h * 0.93)
    click_x_cap = int(w * 0.75)
    kept = []
    for b in buttons:
        if b["y"] >= y_cap:
            logger.debug(f"  [FP-filter] drop y={b['y']} (y_cap={y_cap}) status={b.get('status')}")
            continue
        cx = b.get("click_x")
        if cx is not None and cx >= click_x_cap:
            logger.debug(f"  [FP-filter] drop click_x={cx} (cap={click_x_cap}) status={b.get('status')}")
            continue
        kept.append(b)
    return kept


def detect_buttons(ss):
    """Detect follow buttons with dynamic x-column discovery.
    2026-04-21 #281 修正: legacy 単列 (btn_x=1195) は新 /followers UI (real click_x≈1305) を取りこぼす。
    scan_only_loop 同様の WIDE scan を primary にし、右半分を 80px 刻みで走査する。
    Tries cached x first, then wide-sweep, then hardcoded _BTN_X (legacy fallback)."""
    global _detected_btn_x
    arr = np.array(ss)
    h, w = arr.shape[:2]

    # 1) Try cached x from previous successful scan (stable within same page)
    if _detected_btn_x is not None:
        buttons = _scan_buttons_at(arr, _detected_btn_x)
        buttons = _apply_false_positive_filter(buttons, h, w)
        if buttons:
            return buttons

    # 2) WIDE scan: 右半分 (55%~95%) を 80px 刻みで走査、近傍 y 重複は除外
    seen_y = set()
    wide_buttons = []
    best_col_x = None
    best_count = 0
    for col_x in range(int(w * 0.55), int(w * 0.95), 80):
        col_detected = _scan_buttons_at(arr, col_x)
        # 最も多く検知した col_x を次回キャッシュ候補にする
        if len(col_detected) > best_count:
            best_count = len(col_detected)
            best_col_x = col_x
        for b in col_detected:
            y = b["y"]
            if any(abs(y - sy) < 40 for sy in seen_y):
                continue
            seen_y.add(y)
            wide_buttons.append(b)
    wide_buttons = _apply_false_positive_filter(wide_buttons, h, w)
    if wide_buttons:
        if best_col_x is not None:
            _detected_btn_x = best_col_x
        return wide_buttons

    # 3) legacy hardcoded _BTN_X (念のため fallback)
    buttons = _scan_buttons_at(arr, _sx(_BTN_X))
    buttons = _apply_false_positive_filter(buttons, h, w)
    if buttons:
        _detected_btn_x = _sx(_BTN_X)
        return buttons

    # 4) Final fallback: auto-scan right half
    found_x = auto_find_btn_x(ss)
    if found_x is not None:
        _detected_btn_x = found_x
        logger.info(f"  auto-detected btn_x={found_x}")
        return _apply_false_positive_filter(_scan_buttons_at(arr, found_x), h, w)

    return []

# Rate limit発生時のスクショ保存先（CEO指示 2026-04-20: 誤検知原因追及用）
_last_rate_limit_screenshot = None


def save_rate_limit_screenshot(after_r, after_g):
    """Rate limit検知時のスクショを共有フォルダに保存。CEO/マーケが原因追及できるようにする。"""
    global _last_rate_limit_screenshot
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = f"rate_limit_{ts}_R{int(after_r)}_G{int(after_g)}.png"
        img = pyautogui.screenshot()
        # 優先: 共有フォルダへ直接（ホスト側が即時アクセス可）
        shared_dir = Path(r"\\VBOXSVR\share\rate_limit_screenshots")
        try:
            shared_dir.mkdir(exist_ok=True)
            shared_path = shared_dir / fname
            img.save(str(shared_path))
            _last_rate_limit_screenshot = str(shared_path)
            logger.warning(f"Rate limit screenshot saved (shared): {shared_path}")
            return str(shared_path)
        except Exception as e:
            logger.warning(f"shared save failed: {e}, fallback local")
        # フォールバック: VMローカル
        local_dir = BOT_DIR / "rate_limit_screenshots"
        local_dir.mkdir(exist_ok=True)
        local_path = local_dir / fname
        img.save(str(local_path))
        _last_rate_limit_screenshot = str(local_path)
        logger.warning(f"Rate limit screenshot saved (local): {local_path}")
        return str(local_path)
    except Exception as e:
        logger.error(f"Screenshot save failed: {e}")
        return None


def check_rate_limit_after_click(after_r, after_g, dark_fail_count):
    """
    Rate limit detection based on click result colors.
    2026-04-20 CEO検証により、楽天は実際には100件/hrでrate_limitをかけていない。
    本検知は「クリック座標がズレてグレー領域を踏む」false positive の温床だった。
    閾値を 3 → 20 に引き上げ、判定色域も narrow 化する。
    """
    is_pink_success = (after_r > 70 and after_g < 80 and (after_r - after_g) > 30)
    if is_pink_success:
        dark_fail_count = 0
        return False, dark_fail_count

    # 判定範囲を narrow 化：80-130 のみ（旧: 50-160）
    # 楽天の実際の rate_limit モーダル色サンプル待ちだが、暫定で narrow 化
    is_suspicious = (80 < after_r < 130 and 80 < after_g < 130 and abs(after_r - after_g) < 10)
    if is_suspicious:
        dark_fail_count += 1
        if dark_fail_count >= 20:  # 旧 3 → 20（クリック座標ズレ時の誤検知を抑制）
            return True, dark_fail_count
    else:
        dark_fail_count = 0
    return False, dark_fail_count


def close_rate_limit_dialog():
    """Rate limit detected - wait 3 seconds so user can see, then close Chrome.
    NOTE: Chrome close is NOT done here. Caller must use finally block."""
    logger.warning("!!! Rate limit detected - waiting 3 seconds !!!")
    time.sleep(3)
    # Chrome close is handled in run_follow's finally block (C. 単一化)


# ==================== Click Cache ====================

class ClickCache:
    def __init__(self):
        self.clicked_positions = []
    def is_duplicate(self, x, y):
        for cx, cy, _ in self.clicked_positions:
            if abs(cx-x) < DUPLICATE_THRESHOLD and abs(cy-y) < DUPLICATE_THRESHOLD:
                return True
        return False
    def add(self, x, y):
        self.clicked_positions.append((x, y, time.time()))
    def clear_after_scroll(self):
        self.clicked_positions.clear()


# ==================== Follow Execution ====================

def follow_on_current_page(limit_remaining, dry_run, fail_stats=None):
    """Follow users on the currently open follower modal."""
    cache = ClickCache()
    success = 0
    fail = 0
    skip = 0
    consecutive_skip = 0
    if fail_stats is None:
        fail_stats = FailStats()

    # A. stop_reason優先順位: rate_limit > no_button > next_seed > done
    # 一度rate_limitを踏んだらフラグを立て、以降の判定で上書きしない
    hit_rate_limit = False

    global _detected_btn_x
    _detected_btn_x = None  # Reset for each new page
    dark_fail_count = 0
    scroll_pos = (_sx(_SCROLL_X), _sy(_SCROLL_Y))
    # ⑥ APS per-seed tracking (skip は含めない: already_followed は試行コストなし)
    seed_attempts = 0
    seed_success = 0

    for rnd in range(limit_remaining * 3):
        if success >= limit_remaining or should_stop():
            break

        ss = pyautogui.screenshot()
        buttons = detect_buttons(ss)

        for btn in buttons:
            if success >= limit_remaining or should_stop():
                break
            if btn["status"] == "following":
                skip += 1; consecutive_skip += 1
                fail_stats.record("already_followed")
                continue
            # 2026-04-21 #281: per-button centroid を優先。なければ列フォールバック
            click_x_per_btn = btn.get("click_x") or _detected_btn_x or _sx(_BTN_X)
            if cache.is_duplicate(click_x_per_btn, btn["y"]):
                skip += 1; continue

            if dry_run:
                success += 1; consecutive_skip = 0
                fail_stats.record_success()
                logger.info(f"  [dry-run] +{success} (y={btn['y']}) cx={click_x_per_btn}")
                continue

            fail_stats.record_attempt()
            seed_attempts += 1
            click_x = click_x_per_btn
            col_x_legacy = _detected_btn_x or _sx(_BTN_X)
            click_dx = click_x - col_x_legacy  # 診断用: centroid と legacy 列のズレ幅
            pyautogui.click(click_x, btn["y"])
            cache.add(click_x, btn["y"])
            time.sleep(random.uniform(CLICK_COOLDOWN, CLICK_COOLDOWN_MAX))

            # verify: 短待機(0.3s)後に多点サンプリング
            time.sleep(0.3)
            ss_after = pyautogui.screenshot()

            # 多点verify（方式B）
            verify_success, pink_ratio, verify_detail = verify_follow_success(ss_after, click_x, btn["y"])
            # 旧方式も参照用に取得
            after_r, after_g, _ = get_region_color(ss_after, click_x, btn["y"])

            # 2026-04-21 #281: verify fail で "変化なし (ほぼ白)" なら centroid 再クリックを 1 回試行
            # (ボタンに当たらなかったケースの救済。UI遅延でも間に合う)
            if (not verify_success) and after_r > 235 and after_g > 235:
                logger.info(f"  retry@centroid cx={click_x} dx={click_dx:+d} (after=({after_r:.0f},{after_g:.0f}))")
                time.sleep(0.5)
                # ボタン位置を再スキャン (スクロール無しなら同じ行にあるはず)
                ss_rescan = pyautogui.screenshot()
                arr_rescan = np.array(ss_rescan)
                # 2026-04-22 CRITICAL FIX (CEO目撃 22:15-22:17 で5回アンフォロー発生):
                # verify_follow_success() の 9点サンプリングがピンク flag icon を外し、
                # "フォロー中" 状態にも関わらず False を返すケースあり。そのまま retry@centroid が
                # 走ると "フォロー中" ボタンを再度クリック→アンフォロー発動。累計で減っていく致命バグ。
                # retry click 前に detect_buttons で「実際の現在ステータス」を判定し、
                # "following" なら retry 完全スキップ（単純pink閾値1pxだと誤検知するため、
                # 本番と同じ pink_count > 3 または reddish_count > 15 の閾値ロジックを使う）。
                buttons_rescan = detect_buttons(ss_rescan)
                rescan_status = None
                rescan_cx = None
                for b_rescan in buttons_rescan:
                    if abs(b_rescan["y"] - btn["y"]) < 20:  # same row
                        rescan_status = b_rescan["status"]
                        rescan_cx = b_rescan.get("click_x")
                        break
                if rescan_status == "following":
                    # First click succeeded - button is now "フォロー中". DO NOT CLICK AGAIN.
                    verify_success = True
                    pink_ratio = 1.0  # conservative: treat as full pink confidence
                    verify_detail = {"retry_skipped": "already_following",
                                     "rescan_cx": int(rescan_cx) if rescan_cx is not None else None}
                    logger.info(f"  RETRY SKIP: rescan status='following' (unfollow-guard)")
                else:
                    # Still "not_following" (or row not detected) → legitimate miss, retry OK
                    new_cx = _compute_btn_centroid_x(
                        arr_rescan, max(0, btn['y'] - 15), max(0, click_x - 70),
                        min(arr_rescan.shape[1], click_x + 70), "not_following"
                    )
                    if new_cx is not None and abs(new_cx - click_x) < 120:
                        pyautogui.click(new_cx, btn["y"])
                        click_x = new_cx  # 以降の verify/evidence はこの座標で
                        time.sleep(random.uniform(CLICK_COOLDOWN, CLICK_COOLDOWN_MAX))
                        time.sleep(0.4)
                        ss_after = pyautogui.screenshot()
                        # 2026-04-22 二重ガード: 2回目click後も detect_buttons で following 状態確認
                        buttons_after2 = detect_buttons(ss_after)
                        following_after2 = False
                        for b_a2 in buttons_after2:
                            if abs(b_a2["y"] - btn["y"]) < 20 and b_a2["status"] == "following":
                                following_after2 = True
                                break
                        verify_success, pink_ratio, verify_detail = verify_follow_success(ss_after, click_x, btn["y"])
                        if (not verify_success) and following_after2:
                            verify_success = True
                            pink_ratio = 1.0
                            verify_detail = {"retry_confirmed_following": True}
                        after_r, after_g, _ = get_region_color(ss_after, click_x, btn["y"])
                        if verify_success:
                            logger.info(f"  retry OK (centroid hit) new_cx={new_cx}")

            if verify_success:
                success += 1; seed_success += 1; consecutive_skip = 0; dark_fail_count = 0
                fail_stats.record_success()
                logger.info(f"  OK +{success} (y={btn['y']}) cx={click_x} dx={click_dx:+d} pink_ratio={pink_ratio:.2f}")
                # 成功証跡も保存（対比較用、最初の10件）
                if success <= 10:
                    try:
                        prefix = f"ok_{success:03d}"
                        ss_after.save(str(fail_stats.evidence_path / f"{prefix}_after.png"))
                        meta = FailStats._normalize_for_json({
                            "click_x": click_x, "click_y": btn['y'],
                            "pink_ratio": pink_ratio,
                            "verify_detail": verify_detail,
                            "after_rgb": [after_r, after_g],
                            "seed_user": getattr(follow_on_current_page, '_current_seed', 'unknown'),
                            "scroll_idx": rnd,
                        })
                        (fail_stats.evidence_path / f"{prefix}_meta.json").write_text(
                            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"  OK evidence save failed: {e}")
            else:
                fail += 1; consecutive_skip += 1

                # Detailed fail classification (E'' 6分類)
                if 50 < after_r < 160 and 50 < after_g < 160 and abs(after_r - after_g) < 20:
                    reason = "rate_limit_detected"
                elif after_r < 30 and after_g < 30:
                    reason = "ui_mismatch"
                elif after_r > 240 and after_g > 240:
                    # verify白(255,255)→ボタン状態変化なし。5分類に分解
                    arr_a = np.array(ss_after)
                    scan_left = max(0, click_x - 50)
                    scan_right = min(arr_a.shape[1], click_x + 50)
                    btn_row = arr_a[max(0,btn['y']-15):min(arr_a.shape[0],btn['y']+15), scan_left:scan_right]

                    if btn_row.size == 0:
                        reason = "row_alignment_mismatch"
                    else:
                        row_r = btn_row[:,:,0].mean()
                        row_g = btn_row[:,:,1].mean()

                        # 周辺にピンクがあるか（±50px幅で探索）
                        wide_scan = arr_a[max(0,btn['y']-10):min(arr_a.shape[0],btn['y']+10),
                                          max(0,click_x-80):min(arr_a.shape[1],click_x+80)]
                        wide_r = wide_scan[:,:,0].mean() if wide_scan.size > 0 else 255
                        wide_g = wide_scan[:,:,1].mean() if wide_scan.size > 0 else 255

                        if is_pink(row_r, row_g):
                            reason = "already_followed_missed"
                        elif is_pink(wide_r, wide_g):
                            # ボタンはピンクだがverify点がズレている
                            reason = "button_state_changed_but_verify_missed"
                        elif abs(row_r - 255) < 10 and abs(row_g - 255) < 10:
                            # bbox中心も白→ボタン外の白領域をクリック
                            reason = "verify_sample_on_white_area"
                        elif abs(row_r - after_r) > 30:
                            reason = "bbox_misaligned"
                        else:
                            reason = "verify_sample_out_of_button"
                else:
                    reason = "ui_mismatch"

                # ⑮ verify失敗種類別retry (re-click不要・re-verifyのみ)
                # 2026-05-05 Phase A-3: white_area retry も detect_buttons ベースに強化
                #   旧実装は 0.8s wait + verify_follow_success のみだったが、
                #   sampling点が button bounds 外なら何度待っても white のまま。
                #   detect_buttons で「same row が following 状態か」を判定する方が信頼度高い
                _retry_ok = False
                if reason in ("verify_sample_on_white_area", "verify_sample_out_of_button"):
                    # 共通retry: detect_buttons で「same row が following 状態か」を判定
                    time.sleep(0.5)  # UI 描画安定化
                    _ss_r = pyautogui.screenshot()
                    _btns_r = detect_buttons(_ss_r)
                    _near = min(_btns_r, key=lambda b: abs(b["y"] - btn["y"]), default=None)
                    if _near and _near.get("status") == "following" and abs(_near["y"] - btn["y"]) < 30:
                        _retry_ok = True
                        logger.info(f"  [⑮RETRY-{reason[15:18].upper()}] nearest=following dy={_near['y']-btn['y']}")
                    else:
                        # detect_buttons で確認できない場合の fallback: 旧 verify_follow_success
                        _rv_ok, _, _ = verify_follow_success(_ss_r, click_x, btn["y"])
                        if _rv_ok:
                            _retry_ok = True
                            logger.info(f"  [⑮RETRY-FALLBACK] re-verify OK ({reason})")

                if _retry_ok:
                    fail -= 1; success += 1; seed_success += 1
                    consecutive_skip = 0; dark_fail_count = 0
                    fail_stats.record_success()
                    logger.info(f"  [⑮RETRY] success補正 total={success}")
                    continue

                fail_stats.record(reason)
                logger.info(f"  FAIL (y={btn['y']}) after=({after_r:.0f},{after_g:.0f}) reason={reason}")

                # 証跡保存（最初の20件）+ verify_detail
                fail_stats.save_evidence(
                    ss_before=ss, ss_after=ss_after,
                    click_x=click_x, click_y=btn['y'],
                    btn_region={"scan_x1": scan_left if 'scan_left' in dir() else 0,
                                "scan_x2": scan_right if 'scan_right' in dir() else 0,
                                "y_center": btn['y'],
                                "verify_detail": verify_detail,
                                "pink_ratio": pink_ratio},
                    after_r=after_r, after_g=after_g,
                    seed_user=getattr(follow_on_current_page, '_current_seed', 'unknown'),
                    scroll_idx=rnd,
                    row_idx=buttons.index(btn) if btn in buttons else -1,
                )

                # CRITICAL: Rate limit detection via dark overlay color
                is_limit, dark_fail_count = check_rate_limit_after_click(after_r, after_g, dark_fail_count)
                if is_limit:
                    fail_stats.record("rate_limit_detected")
                    hit_rate_limit = True
                    logger.warning(f"!!! RATE LIMIT DETECTED (dark={after_r:.0f},{after_g:.0f}) !!!")
                    # CEO指示 2026-04-20: 誤検知原因追及のためスクショ保存
                    save_rate_limit_screenshot(after_r, after_g)
                    close_rate_limit_dialog()
                    # A. rate_limit は最優先stop_reason。即return
                    return success, fail, skip, "rate_limit_detected", fail_stats

        if consecutive_skip >= MAX_CONSECUTIVE_SKIP:
            return success, fail, skip, "next_seed", fail_stats

        # SAFETY: if no buttons found at all, possible dialog blocking view
        if len(buttons) == 0:
            fail_stats.record("no_button_detected")
            no_button_rounds = getattr(follow_on_current_page, '_no_btn', 0) + 1
            follow_on_current_page._no_btn = no_button_rounds
            if no_button_rounds >= 5:
                logger.warning(f"!!! No buttons found for {no_button_rounds} rounds !!!")
                follow_on_current_page._no_btn = 0
                # D. rate_limit後の副作用かどうかを分離
                if hit_rate_limit:
                    # rate_limitの副作用→stop_reasonはrate_limitを保持
                    return success, fail, skip, "rate_limit_detected", fail_stats
                else:
                    return success, fail, skip, "no_button_detected", fail_stats
        else:
            follow_on_current_page._no_btn = 0

        pyautogui.moveTo(*scroll_pos)
        time.sleep(0.3)
        pyautogui.scroll(SCROLL_AMOUNT)
        cache.clear_after_scroll()
        time.sleep(1.5)

        # ⑥ APS早期次seed切替: APS_MIN_SAMPLE回以上試行かつAPS>閾値 → 次seedへ
        if seed_attempts >= APS_MIN_SAMPLE and seed_success == 0:
            aps_now = float('inf')
            logger.info(f"  [APS-BAIL] attempts={seed_attempts} success=0 (inf) → 次seedへ")
            return success, fail, skip, "poor_aps", fail_stats
        elif seed_attempts >= APS_MIN_SAMPLE:
            aps_now = seed_attempts / seed_success
            if aps_now > APS_BAIL_THRESHOLD:
                logger.info(f"  [APS-BAIL] aps={aps_now:.1f} attempts={seed_attempts} success={seed_success} → 次seedへ")
                return success, fail, skip, "poor_aps", fail_stats

    return success, fail, skip, "done", fail_stats


def _bot_check_dead_zone(force: bool = False) -> tuple[bool, str]:
    """
    ⑦ Bot内Dead Zone早期脱出チェック (2026-05-03 実装)
    VM内でrun_follow()が呼ばれた際に、dead zoneパターンを検知して即リターン。
    HOST側のcheck_dead_zone()と同ロジック。c24ロールオフbypass付き。

    force=True: dead zone判定を無条件にスキップ（CEO手動 / launcher --force経由）
    """
    if force:
        return False, "force override (--force)"
    from datetime import datetime, timedelta as _td
    C24_RECOVERY_DROP = 80
    try:
        if not LOG_PATH.exists():
            return False, "log not found"
        logs = json.loads(LOG_PATH.read_text(encoding="utf-8"))
        if len(logs) < 2:
            return False, "insufficient data"

        recent = logs[-3:]
        dead_count = 0
        reasons = []
        last_dead_ts = None

        for entry in recent:
            s = entry.get("success", 0)
            fs = entry.get("fail_stats", {})
            rl = fs.get("rate_limit_detected", 0)
            modal_fail = fs.get("modal_open_failed", 0)
            af = fs.get("already_followed", 0)
            ts_str = entry.get("timestamp", "?")
            if s > 0:
                continue
            if modal_fail > 100:
                dead_count += 1; reasons.append(f"{ts_str[:16]}:modal={modal_fail}"); last_dead_ts = ts_str; continue
            if rl > 10:
                dead_count += 1; reasons.append(f"{ts_str[:16]}:RL={rl}"); last_dead_ts = ts_str; continue
            if af > 200:
                dead_count += 1; reasons.append(f"{ts_str[:16]}:af={af}"); last_dead_ts = ts_str

        if dead_count < 2:
            return False, f"dead={dead_count}/3 OK"

        # c24ロールオフbypass
        if last_dead_ts:
            now = datetime.now()
            dead_ts = datetime.fromisoformat(last_dead_ts)
            cutoff_dead = dead_ts - _td(hours=24)
            c24_at_dead = sum(e.get("success", 0) for e in logs
                              if cutoff_dead <= datetime.fromisoformat(e.get("timestamp", "2000-01-01")) <= dead_ts)
            cutoff_now = now - _td(hours=24)
            c24_now = sum(e.get("success", 0) for e in logs
                          if cutoff_now <= datetime.fromisoformat(e.get("timestamp", "2000-01-01")) <= now)
            drop = c24_at_dead - c24_now
            if drop >= C24_RECOVERY_DROP:
                return False, f"dead_zone bypassd: c24 {c24_at_dead}->{c24_now} (-{drop}>={C24_RECOVERY_DROP})"

        return True, f"DEAD_ZONE: {', '.join(reasons)}"
    except Exception as e:
        return False, f"dead_zone check err: {e}"


def run_follow(limit, dry_run=False, force=False):
    """Full auto follow using seed users (genre order)."""
    clear_stop_flag()
    state = load_state()

    # ⑦ Dead Zone早期脱出 (2026-05-03): Chromeを起動する前にチェック
    _dz, _dz_reason = _bot_check_dead_zone(force=force)
    if _dz:
        logger.warning(f"[DEAD_ZONE] early exit: {_dz_reason}")
        logger.warning(f"[DEAD_ZONE] c24ロールオフまで待機。--force は --no-session-cap を指定して上書き可能")
        # dead_zone中はappend_logを書かない (同一内容の累積を避けるため)
        return
    else:
        logger.info(f"[DEAD_ZONE_CHECK] pass: {_dz_reason}")

    if not SEED_USERS_PATH.exists():
        logger.error("seed_users.json not found. Run --scrape first.")
        return

    seeds = json.loads(SEED_USERS_PATH.read_text(encoding="utf-8"))
    # 2026-05-02 改善方法⑧: ladies_fashion優先 + 全11ジャンル走査
    # FOLLOW_GENRE_PRIORITY内のジャンルでseed_users.jsonに存在するもののみ走査
    # 不足分はseed_users.jsonの追加ジャンルを末尾に追加（将来の互換性）
    genre_order = [g for g in FOLLOW_GENRE_PRIORITY if g in seeds]
    # Edge case: seed_users.json に PRIORITY 未定義のジャンルがあれば末尾に追加
    for g in seeds:
        if g not in genre_order:
            genre_order.append(g)
    logger.info(f"  Follow genre order: {genre_order}")

    # Build ordered list: ladies_fashion[0..N], interior[0..N], ...
    ordered_seeds = []
    for gk in genre_order:
        for u in seeds.get(gk, []):
            ordered_seeds.append({"user": u, "genre": gk})

    if not ordered_seeds:
        logger.error("No seed users. Run --scrape first.")
        return

    # Resume position
    progress = state.get("seed_progress", {"genre_index": 0, "user_index": 0})
    start_pos = 0
    gi_target = progress.get("genre_index", 0)
    ui_target = progress.get("user_index", 0)
    count_in_genre = 0
    for i, s in enumerate(ordered_seeds):
        gidx = genre_order.index(s["genre"]) if s["genre"] in genre_order else 0
        if gidx == gi_target:
            if count_in_genre == ui_target:
                start_pos = i
                break
            count_in_genre += 1

    total_success = 0
    total_fail = 0
    total_skip = 0
    stop_reason = ""

    logger.info(f"=== Follow RPA Start target={limit} {'(dry-run)' if dry_run else ''} ===")
    logger.info(f"  Seeds: {len(ordered_seeds)}, start=#{start_pos}")

    if not dry_run:
        # Launch Chrome automatically
        launch_chrome()
        time.sleep(3)
        # heartbeat startup
        write_heartbeat("startup", force=True)

        # 2026-05-05 Phase 2-1: ログイン状態を Chrome起動直後にチェック
        login_ok, login_detail = check_login_status()
        logger.info(f"[LOGIN_CHECK] ok={login_ok} {login_detail}")
        state["login_status"] = "ok" if login_ok else "expired"
        state["login_check_at"] = datetime.now().isoformat()
        save_state(state)
        if not login_ok:
            logger.error(f"[LOGIN_EXPIRED] ROOM session lost. Aborting run.")
            # Slack通知（HOST側経由・shared folder のフラグファイルで通知要求）
            try:
                flag_path = Path(r"\\VBOXSVR\share\login_expired_flag.json")
                flag_path.write_text(json.dumps({
                    "ts": datetime.now().isoformat(),
                    "detail": login_detail,
                }, ensure_ascii=False), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[LOGIN_EXPIRED] flag write failed: {e}")
            close_chrome()
            state["stop_reason"] = "login_expired"
            save_state(state)
            return

    # E'' 計測基盤: セッション全体のfail_stats
    session_fail_stats = FailStats()

    # C. Chrome終了処理を単一化: try/finally で1箇所のみ
    # 2026-05-03: SESSION_SEED_BUDGETで1セッションのseed走査数を制限
    # 447seeds全走査は120min超→200seeds制限で~60minに短縮
    budget = min(SESSION_SEED_BUDGET, len(ordered_seeds))
    logger.info(f"  Seed budget: {budget}/{len(ordered_seeds)}")
    try:
        for i in range(budget):
            if total_success >= limit or should_stop():
                break

            idx = (start_pos + i) % len(ordered_seeds)
            seed = ordered_seeds[idx]

            logger.info(f"\n--- [{seed['genre']}] {seed['user']} (#{idx}) ---")
            # 2026-05-05 Phase 2-2 + C-1: heartbeat更新（30秒スロットル）+ fail_stats by group
            try:
                _hb_extra = {"fail_stats": dict(session_fail_stats.counts)} if 'session_fail_stats' in dir() else {}
            except Exception:
                _hb_extra = {}
            write_heartbeat("navigate", current_seed=seed["user"],
                            success=total_success, fail=total_fail, extra=_hb_extra)

            if not dry_run:
                nav_ok, nav_reason = navigate_to_seed_user(seed["user"])
                if not nav_ok:
                    logger.info(f"  Skipping {seed['user']} - nav failed (reason={nav_reason})")
                    # 2026-04-22 Phase 2: 前提チェック不通過は即停止（他 seed も同様に失敗するため）
                    if nav_reason in ("foreground_mismatch", "input_target_uncertain"):
                        logger.error(f"  [PRECONDITION ABORT] {nav_reason} — stopping run to prevent blind loop")
                        session_fail_stats.record(nav_reason)
                        state["stop_reason"] = nav_reason
                        save_state(state)
                        break
                    session_fail_stats.record(nav_reason or "modal_open_failed")
                    session_fail_stats.add_weak_seed(seed["user"])
                    continue

            remaining = limit - total_success
            follow_on_current_page._current_seed = seed["user"]
            success, fail, skip, reason, page_stats = follow_on_current_page(remaining, dry_run, session_fail_stats)

            total_success += success
            total_fail += fail
            total_skip += skip

            logger.info(f"  +{success} follows (reason: {reason})")

            # Save progress
            gidx = genre_order.index(seed["genre"]) if seed["genre"] in genre_order else 0
            ui = 0
            for s in ordered_seeds[:idx+1]:
                if s["genre"] == seed["genre"]:
                    ui += 1
            state["seed_progress"] = {"genre_index": gidx, "user_index": ui}

            # A. stop_reason優先順位: rate_limit_detected > stop_flag > target_reached
            # CEO指示 2026-04-21: no_button_detected は単一seedの枯渇なので次seedへ継続（runを止めない）
            # 100件達成 or rate_limit 検知のどちらかまで回し続ける
            if reason == "rate_limit_detected":
                stop_reason = "rate_limit_detected"
                break
            if should_stop():
                stop_reason = "stop_flag"
                break
            # no_button_detected, next_seed, done, poor_aps は continue (次seedへ)

        if not stop_reason:
            stop_reason = "target_reached" if total_success >= limit else "all_seeds_done"

    finally:
        # 2026-05-05 Phase 2-2: shutdown heartbeat
        write_heartbeat("shutdown", success=total_success, fail=total_fail, force=True,
                        extra={"stop_reason": stop_reason})
        # C. Chrome close は必ずここ1箇所のみ
        if not dry_run:
            close_chrome()

    # B. 指標定義統一
    state["total_success"] = state.get("total_success", 0) + total_success
    state["total_fail"] = state.get("total_fail", 0) + total_fail
    state["stop_reason"] = stop_reason
    if total_success > 0:
        state["last_success_at"] = datetime.now().isoformat()
    save_state(state)

    # E'' 計測: fail_stats をログとstateに記録
    stats = session_fail_stats.summary_dict()

    append_log({
        "timestamp": datetime.now().isoformat(),
        "success": total_success,
        "fail_actionable": stats["fail_actionable"],
        "skip_total": stats["skip_total"],
        "fail_total_including_skip": stats["fail_total"],
        "stop_reason": stop_reason,
        "fail_stats": stats["fail_breakdown"],
        "screenshot_path": _last_rate_limit_screenshot,  # CEO指示 2026-04-20
        "metrics": {
            "total_attempts": stats["total_attempts"],
            "attempts_per_success": stats["attempts_per_success"],
            "most_frequent_reason": stats["most_frequent_stop_reason"],
            "weak_seed_count": stats["weak_seed_count"],
            "weak_seeds": stats["weak_seeds"],
        },
    })

    state["last_fail_stats"] = stats["fail_breakdown"]
    state["last_metrics"] = {
        "attempts_per_success": stats["attempts_per_success"],
        "weak_seed_count": stats["weak_seed_count"],
    }
    save_state(state)

    # B. 指標定義統一: Result行も統一形式で出力
    logger.info(f"\n{'='*50}")
    logger.info(f"Result: success={total_success} fail_actionable={stats['fail_actionable']} skip_total={stats['skip_total']} fail_total={stats['fail_total']}")
    logger.info(f"Stop reason: {stop_reason}")
    session_fail_stats.log_summary()
    logger.info(f"{'='*50}")


# ==================== Main ====================

def scan_only_loop(max_screens=10):
    """2026-04-21 #281 マーケ指示対応: 検知精度計測モード。click せず detect のみ実行。
    可視画面 vs bot認識 の完全一致を測定するための evidence bundle を出力する。

    各 screen ごとに:
      - screenshot PNG 保存
      - detect_buttons の生結果 (status/y/click_x) + scan range + auto_btn_x を JSONL 追記
    完了後に summary.json を書き出す。emergency_flag / click / API叩きナシで安全。
    """
    import shutil
    global _detected_btn_x
    _detected_btn_x = None

    # 2026-04-21 #281: cmd window を最小化して Chrome を全画面で写す
    # (さもないと cmd 窓が screenshot に覆いかぶさって検知不能になる)
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            time.sleep(1.0)
        # Chrome 窓を最前面 + 最大化
        user32 = ctypes.windll.user32
        # FindWindowW で Chrome メインウィンドウ探索（タイトル末尾 "Google Chrome"）
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        GetWindowText = user32.GetWindowTextW
        GetWindowTextLength = user32.GetWindowTextLengthW
        IsWindowVisible = user32.IsWindowVisible
        chrome_hwnd_holder = []
        def _enum_cb(hwnd_, lparam):
            if not IsWindowVisible(hwnd_):
                return True
            length = GetWindowTextLength(hwnd_)
            if length == 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd_, buff, length + 1)
            title = buff.value
            if "Google Chrome" in title or "Chrome" in title:
                chrome_hwnd_holder.append(hwnd_)
            return True
        EnumWindows(EnumWindowsProc(_enum_cb), 0)
        if chrome_hwnd_holder:
            chrome_hwnd = chrome_hwnd_holder[0]
            user32.ShowWindow(chrome_hwnd, 3)  # SW_MAXIMIZE
            user32.SetForegroundWindow(chrome_hwnd)
            time.sleep(1.5)
            logger.info(f"[scan-only] Chrome hwnd={chrome_hwnd} brought to front + maximized")
        else:
            logger.warning("[scan-only] Chrome window not found by EnumWindows")
    except Exception as e:
        logger.warning(f"[scan-only] window arrange failed: {e}")

    ts_tag = datetime.now().strftime('%Y%m%d_%H%M%S')
    evidence_dir = BOT_DIR / "evidence" / f"scan_only_{ts_tag}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    # 2026-04-21 #281: host 側で目視レビューできるよう share/ にも mirror する
    mirror_dir = None
    try:
        mirror_dir = Path(r"\\VBOXSVR\share") / "scan_only_evidence" / f"scan_only_{ts_tag}"
        mirror_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[scan-only] mirror_dir = {mirror_dir}")
    except Exception as e:
        logger.warning(f"[scan-only] mirror_dir create failed: {e}")
        mirror_dir = None
    dump_path = evidence_dir / "detect_dump.jsonl"
    summary = {"started_at": datetime.now().isoformat(), "screens": [], "mirror_dir": str(mirror_dir) if mirror_dir else None}
    scroll_pos = (_sx(_SCROLL_X), _sy(_SCROLL_Y))
    logger.info(f"[scan-only] evidence_dir = {evidence_dir}")

    for i in range(max_screens):
        if should_stop():
            logger.info("[scan-only] stop_flag detected, breaking")
            break
        ss = pyautogui.screenshot()
        arr = np.array(ss)
        h, w = arr.shape[:2]

        # 2026-04-21 #281: scan-only では WIDE scan で right half 全体をスイープする
        # （列固定の detect_buttons だと auto_btn_x miss した時に空振る）
        wide_buttons_raw = []
        # 画面右半分を 100px 刻みで複数列走査、同じ y 近傍の重複は除外
        seen_y = set()
        for col_x in range(int(w * 0.55), int(w * 0.95), 80):
            col_detected = _scan_buttons_at(arr, col_x)
            for b in col_detected:
                # y が既検知と 30px 以内なら重複とみなす
                y = b["y"]
                if any(abs(y - sy) < 40 for sy in seen_y):
                    continue
                seen_y.add(y)
                wide_buttons_raw.append({**b, "col_x": col_x})
        # 2026-04-21 #281: false-positive filter
        # (A) y >= int(h*0.93) は Windows taskbar/Chrome status bar にかぶる
        # (B) click_x >= int(w*0.75) (=1440@1920) は スクロールバー/サムネ右端アーチファクト
        #     実ボタンは col_x≈1296 中央 click_x≈1305 なので 1440 以上は異常
        # y_cap=int(h*0.93) → 845@909 (real button y=801 kept, false y=847 excluded)
        # click_x_cap=int(w*0.75) → 1440@1920 (real click_x≈1305, artifact at 1441 excluded)
        y_cap = int(h * 0.93)
        click_x_cap = int(w * 0.75)
        wide_buttons = []
        filtered_out = []
        for b in wide_buttons_raw:
            reason = None
            if b["y"] >= y_cap:
                reason = "y_below_content_area"
            elif b.get("click_x") is not None and b["click_x"] >= click_x_cap:
                reason = "click_x_too_right"
            if reason:
                filtered_out.append({**b, "filter_reason": reason})
            else:
                wide_buttons.append(b)
        # レガシー detect_buttons 結果と比較するため両方保存
        buttons_legacy = detect_buttons(ss)
        buttons = wide_buttons  # wide を primary とする
        visible_follow_now = sum(1 for b in buttons if b["status"] == "not_following")
        visible_following = sum(1 for b in buttons if b["status"] == "following")
        legacy_follow_now = sum(1 for b in buttons_legacy if b["status"] == "not_following")
        legacy_following = sum(1 for b in buttons_legacy if b["status"] == "following")

        shot_path = evidence_dir / f"scan_{i:03d}.png"
        try:
            ss.save(str(shot_path))
        except Exception as e:
            logger.warning(f"  screenshot save failed: {e}")
        # 2026-04-21 #281: mirror to share for host-side review
        if mirror_dir is not None:
            try:
                ss.save(str(mirror_dir / f"scan_{i:03d}.png"))
            except Exception as e:
                logger.warning(f"  mirror screenshot save failed: {e}")

        cur_btn_x = _detected_btn_x or _sx(_BTN_X)
        dump = {
            "screen_idx": i,
            "ts": datetime.now().isoformat(),
            "screen_wh": [int(w), int(h)],
            "auto_btn_x": _detected_btn_x,
            "btn_x_used": int(cur_btn_x),
            "scan_y_range": [int(h * 0.22), int(h * 0.93)],
            "wide_scan_x_range": [int(w * 0.55), int(w * 0.95)],
            "buttons_wide": [
                {
                    "y": int(b["y"]),
                    "status": b["status"],
                    "click_x": int(b["click_x"]) if b.get("click_x") is not None else None,
                    "col_x": int(b.get("col_x", 0)),
                    "r": round(float(b["r"]), 1),
                    "g": round(float(b["g"]), 1),
                }
                for b in buttons
            ],
            "buttons_legacy": [
                {
                    "y": int(b["y"]),
                    "status": b["status"],
                    "click_x": int(b["click_x"]) if b.get("click_x") is not None else None,
                    "r": round(float(b["r"]), 1),
                    "g": round(float(b["g"]), 1),
                }
                for b in buttons_legacy
            ],
            "filtered_out": [
                {
                    "y": int(b["y"]),
                    "status": b["status"],
                    "click_x": int(b["click_x"]) if b.get("click_x") is not None else None,
                    "col_x": int(b.get("col_x", 0)),
                    "reason": b.get("filter_reason", ""),
                }
                for b in filtered_out
            ],
            "filter_rules": {"y_cap": y_cap, "click_x_cap": click_x_cap},
            "visible_follow_now": visible_follow_now,
            "visible_following": visible_following,
            "legacy_follow_now": legacy_follow_now,
            "legacy_following": legacy_following,
            "total_detected_wide": len(buttons),
            "total_detected_legacy": len(buttons_legacy),
            "screenshot": shot_path.name,
        }
        with open(dump_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(dump, ensure_ascii=False) + "\n")
        # 2026-04-21 #281: mirror dump to share
        if mirror_dir is not None:
            try:
                with open(mirror_dir / "detect_dump.jsonl", "a", encoding="utf-8") as f:
                    f.write(json.dumps(dump, ensure_ascii=False) + "\n")
            except Exception as e:
                logger.warning(f"  mirror dump write failed: {e}")
        summary["screens"].append({
            "idx": i,
            "visible_follow_now": visible_follow_now,
            "visible_following": visible_following,
            "legacy_follow_now": legacy_follow_now,
            "legacy_following": legacy_following,
            "total_wide": len(buttons),
            "total_legacy": len(buttons_legacy),
            "auto_btn_x": _detected_btn_x,
        })
        logger.info(f"[scan-only] screen {i:03d}: wide(nf={visible_follow_now} f={visible_following}) "
                    f"legacy(nf={legacy_follow_now} f={legacy_following}) btn_x={cur_btn_x}")

        # スクロール (2026-04-21 #281 rev4: mouse wheel on content area)
        # PageDown 方式は click 場所が content frame 外だと focus が Chrome に
        # 行かず失敗する。mouse wheel は mouse 下の要素に直接 scroll event 送るため
        # focus 不要・確実に動く。
        try:
            # 画面中央やや左 (content area の中央付近、ボタン列からは離す)
            mx, my = int(w * 0.40), int(h * 0.50)
            pyautogui.moveTo(mx, my)
            time.sleep(0.2)
            # 複数回 scroll して viewport を確実に下へ（-800 = 約2行分×4回）
            for _ in range(4):
                pyautogui.scroll(-400)
                time.sleep(0.3)
            time.sleep(1.5)
        except Exception as e:
            logger.warning(f"  scroll failed: {e}")

    summary["ended_at"] = datetime.now().isoformat()
    summary["total_screens_scanned"] = len(summary["screens"])
    summary["total_not_following_detected"] = sum(s.get("visible_follow_now", 0) for s in summary["screens"])
    summary["total_following_detected"] = sum(s.get("visible_following", 0) for s in summary["screens"])
    summary["total_not_following_legacy"] = sum(s.get("legacy_follow_now", 0) for s in summary["screens"])
    summary["total_following_legacy"] = sum(s.get("legacy_following", 0) for s in summary["screens"])
    summary_path = evidence_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    # ホスト側アクセス用に share 直下にも summary を書く
    try:
        shared = Path(r"\\VBOXSVR\share\scan_only_latest_summary.json")
        shared.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    # mirror dir にも summary を書く
    if mirror_dir is not None:
        try:
            (mirror_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    print("=" * 60)
    print(f"[scan-only DONE] {len(summary['screens'])} screens")
    print(f"  total not_following detected: {summary['total_not_following_detected']}")
    print(f"  total following detected:     {summary['total_following_detected']}")
    print(f"  evidence: {evidence_dir}")
    print("  MUST review screenshots by eye to count follow-possible items")
    print("  then compare with detect_dump.jsonl visible_follow_now")
    print("=" * 60)
    return summary


def test_3seed_loop(seeds, per_seed_limit=5):
    """2026-04-21 CEO 目視合否テスト: 3 seed x per_seed_limit follow テスト。
    条件:
      - 各 seed の /followers を順番に開く
      - 各 seed で per_seed_limit 件 follow を試行
      - 合計 (3 x per_seed_limit) 件 100% 成功なら PASS、1件でも失敗で FAIL
    用途: bot の可視画面認識精度を CEO が目で追って確認するための専用モード。
    """
    import shutil
    clear_stop_flag()
    global _detected_btn_x
    _detected_btn_x = None

    # Chrome 最前面 + 最大化 (scan_only_loop と同じ処理)
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            time.sleep(1.0)
        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        GetWindowText = user32.GetWindowTextW
        GetWindowTextLength = user32.GetWindowTextLengthW
        IsWindowVisible = user32.IsWindowVisible
        chrome_hwnd_holder = []
        def _enum_cb(hwnd_, lparam):
            if not IsWindowVisible(hwnd_):
                return True
            length = GetWindowTextLength(hwnd_)
            if length == 0:
                return True
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd_, buff, length + 1)
            title = buff.value
            if "Google Chrome" in title or "Chrome" in title:
                chrome_hwnd_holder.append(hwnd_)
            return True
        EnumWindows(EnumWindowsProc(_enum_cb), 0)
        if chrome_hwnd_holder:
            chrome_hwnd = chrome_hwnd_holder[0]
            user32.ShowWindow(chrome_hwnd, 3)  # SW_MAXIMIZE
            user32.SetForegroundWindow(chrome_hwnd)
            time.sleep(1.5)
            logger.info(f"[test-3seed] Chrome hwnd={chrome_hwnd} brought to front + maximized")
        else:
            logger.warning("[test-3seed] Chrome window not found by EnumWindows")
    except Exception as e:
        logger.warning(f"[test-3seed] window arrange failed: {e}")

    ts_tag = datetime.now().strftime('%Y%m%d_%H%M%S')
    evidence_dir = BOT_DIR / "evidence" / f"test_3seed_{ts_tag}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    mirror_dir = None
    try:
        mirror_dir = Path(r"\\VBOXSVR\share") / "test_3seed_evidence" / f"test_3seed_{ts_tag}"
        mirror_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[test-3seed] mirror_dir = {mirror_dir}")
    except Exception as e:
        logger.warning(f"[test-3seed] mirror_dir create failed: {e}")
        mirror_dir = None

    total_expected = len(seeds) * per_seed_limit
    logger.info("=" * 60)
    logger.info(f"[test-3seed] 開始: {len(seeds)} seed x {per_seed_limit} follow = {total_expected} 件")
    logger.info(f"[test-3seed] seeds = {seeds}")
    logger.info(f"[test-3seed] evidence_dir = {evidence_dir}")
    logger.info("=" * 60)

    seeds_results = []
    total_success = 0
    total_fail = 0
    total_skip = 0

    for i, seed in enumerate(seeds):
        logger.info(f"--- seed #{i+1}/{len(seeds)} = {seed} ---")
        if should_stop():
            logger.info("[test-3seed] stop_flag detected, breaking")
            break
        # Navigate (Phase 2: 戻り値 tuple 対応)
        nav_ok, nav_reason = navigate_to_seed_user(seed)
        if not nav_ok:
            logger.error(f"  seed {seed}: navigation failed (reason={nav_reason})")
            seeds_results.append({
                "seed": seed, "navigated": False,
                "success": 0, "fail": 0, "skip": 0,
                "stop_reason": nav_reason or "navigation_failed",
            })
            total_fail += per_seed_limit  # 未試行分は全部 fail 扱い
            # precondition abort は早期 break
            if nav_reason in ("foreground_mismatch", "input_target_uncertain"):
                logger.error(f"  [PRECONDITION ABORT] stop test loop")
                break
            continue

        # この seed の fail_stats は個別に収集
        setattr(follow_on_current_page, '_current_seed', seed)
        success, fail, skip, stop_reason, fail_stats = follow_on_current_page(
            limit_remaining=per_seed_limit, dry_run=False, fail_stats=None,
        )
        logger.info(f"  seed {seed}: success={success}/{per_seed_limit} fail={fail} skip={skip} reason={stop_reason}")

        # per-seed screenshot
        try:
            ss_end = pyautogui.screenshot()
            ss_path = evidence_dir / f"seed{i+1}_{seed}_end.png"
            ss_end.save(str(ss_path))
            if mirror_dir is not None:
                ss_end.save(str(mirror_dir / ss_path.name))
        except Exception as e:
            logger.warning(f"  seed {seed}: end screenshot failed: {e}")

        seeds_results.append({
            "seed": seed, "navigated": True,
            "success": success, "fail": fail, "skip": skip,
            "stop_reason": stop_reason,
            "reached_limit": (success >= per_seed_limit),
        })
        total_success += success
        total_fail += fail
        total_skip += skip

        # seed 切り替え前の待機 (CEO が追える速度)
        if i < len(seeds) - 1:
            logger.info(f"  next seed まで 4s 待機")
            time.sleep(4.0)

    verdict = "PASS" if (total_success == total_expected and total_fail == 0) else "FAIL"
    logger.info("=" * 60)
    logger.info(f"[test-3seed] RESULT: {verdict}")
    logger.info(f"  success = {total_success} / {total_expected}")
    logger.info(f"  fail    = {total_fail}")
    logger.info(f"  skip    = {total_skip}")
    for r in seeds_results:
        logger.info(f"  {r['seed']}: success={r['success']}/{per_seed_limit} fail={r['fail']} skip={r['skip']} stop={r['stop_reason']}")
    logger.info("=" * 60)

    summary = {
        "started_at": ts_tag,
        "ended_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "seeds_tested": seeds,
        "per_seed_limit": per_seed_limit,
        "total_expected": total_expected,
        "total_success": total_success,
        "total_fail": total_fail,
        "total_skip": total_skip,
        "verdict": verdict,
        "seeds_results": seeds_results,
    }
    summary_path = evidence_dir / "summary.json"
    try:
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"  summary save failed: {e}")
    if mirror_dir is not None:
        try:
            (mirror_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"  mirror summary failed: {e}")
    return summary


def main():
    parser = argparse.ArgumentParser(description="Rakuten ROOM Follow RPA (VM)")
    parser.add_argument("--scrape", action="store_true", help="Collect seed users from ranking")
    parser.add_argument("--limit", type=int, default=90)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--scan-only", action="store_true",
                        help="#281 検知精度計測モード: clickせず detect のみ実行")
    parser.add_argument("--max-screens", type=int, default=10,
                        help="scan-only時の画面数 (default 10)")
    parser.add_argument("--test-3seed", action="store_true",
                        help="2026-04-21 CEO 目視テスト: 3 seed x per-seed-limit follow")
    parser.add_argument("--per-seed-limit", type=int, default=5,
                        help="--test-3seed 時の 1 seed あたり follow 数 (default 5)")
    parser.add_argument("--seeds", type=str, default=None,
                        help="--test-3seed 時の seed カンマ区切り (省略時は seed_users.json sweets 先頭3)")
    parser.add_argument("--no-session-cap", action="store_true",
                        help="2026-04-21 CEO 上限検知テスト: SESSION_MIN/MAX cap を無視して --limit をそのまま使う")
    parser.add_argument("--force", action="store_true",
                        help="2026-05-05 Dead Zone check を無視して強制起動（launcher --force から伝達）")
    args = parser.parse_args()

    if args.stop:
        create_stop_flag()
        print("Stop flag created.")
        return
    if args.scrape:
        scrape_seed_users()
        return
    if args.scan_only:
        scan_only_loop(max_screens=args.max_screens)
        return
    if args.test_3seed:
        # seed 決定
        if args.seeds:
            seeds = [s.strip() for s in args.seeds.split(",") if s.strip()]
        else:
            try:
                data = json.loads(SEED_USERS_PATH.read_text(encoding="utf-8"))
                sweets = data.get("sweets", [])
                seeds = sweets[:3]
            except Exception as e:
                print(f"[test-3seed] seed_users.json load failed: {e}")
                return
        if len(seeds) < 1:
            print("[test-3seed] no seeds specified")
            return
        test_3seed_loop(seeds, per_seed_limit=args.per_seed_limit)
        return

    if args.no_session_cap:
        # CEO 指示 2026-04-21: 上限検知テスト — session_limit cap 無視
        effective_limit = args.limit
        print(f"[no-session-cap] 上限無視モード: --limit {args.limit} をそのまま使う")
    else:
        session_limit = random.randint(SESSION_MIN, SESSION_MAX)
        effective_limit = min(args.limit, session_limit)
        # ③ 朝抑制 (2026-05-03 実装): 06:00-10:59 は limit=20 に制限してc24消費を温存
        # 根拠: H22(深夜APS=2.0/朝APS=9.8), H16(23:00-11:00=810件/日最適), ③改善計画
        # --no-session-cap 指定時はこの制限をスキップ（CEO手動テスト用途保護）
        _morning_limit = 20
        _hour = datetime.now().hour
        if 6 <= _hour < 11:
            if effective_limit > _morning_limit:
                print(f"[③ 朝抑制] {_hour:02d}時台 → limit {effective_limit} -> {_morning_limit} (c24温存)")
                effective_limit = _morning_limit
    run_follow(effective_limit, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
