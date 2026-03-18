"""統合通知スクリプト - 全通知を1ファイルで管理
音声キャラクター: VOICEVOX:春日部つむぎ

Usage:
    python ops/notifications/notifier.py approval       # 確認をお願いします（PermissionRequest Hook自動発火）
    python ops/notifications/notifier.py dev_done        # 修正終わりました（COO手動実行・全作業完了時のみ1回）
    python ops/notifications/notifier.py post_done       # 投稿が完了しました
    python ops/notifications/notifier.py like_done       # いいねが完了しました
    python ops/notifications/notifier.py follow_done     # フォローが完了しました
    python ops/notifications/notifier.py error           # エラーが発生しました
    python ops/notifications/notifier.py test_all        # 全通知を順番にテスト
"""
import os
import sys
import time
import datetime
import subprocess
import winsound

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(SCRIPT_DIR, "sounds")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "notifier.log")

# approval: PermissionRequest Hook（承認前に自動発火、遅延不要）
# dev_done: COO手動実行（全作業完了時のみ1回）
HOOK_DELAY_SEC = 0

# 通知定義: (wavファイル名 or None, フォールバック音タイプ, メッセージ)
NOTIFICATIONS = {
    "post_done":   (None,             "Asterisk",    "投稿が完了しました"),
    "like_done":   (None,             "Asterisk",    "いいねが完了しました"),
    "follow_done": (None,             "Asterisk",    "フォローが完了しました"),
    "dev_done":    ("dev_done.wav",   "Asterisk",    "修正終わりました"),
    "approval":    ("approval.wav",   "Hand",        "確認をお願いします"),
    "error":       (None,             "Exclamation", "エラーが発生しました。ログを確認してください"),
}


def log(msg: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")


def _play_wav(wav_path: str, async_mode: bool = False) -> bool:
    """winsoundでwavファイルを再生（PowerShell不要・即時再生）"""
    try:
        if async_mode:
            winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            winsound.PlaySound(wav_path, winsound.SND_FILENAME)
        return True
    except Exception as e:
        log(f"WAV_ERROR: {wav_path} {e}")
        return False


def _play_system_sound_and_tts(sound_type: str, message: str) -> bool:
    """システム音+TTS（wavファイルがない通知用フォールバック）"""
    ps_cmd = (
        f"[System.Media.SystemSounds]::{sound_type}.Play();"
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        f"$s.Speak('{message}')"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            timeout=15,
            creationflags=0x08000000,
        )
        return result.returncode == 0
    except Exception as e:
        log(f"TTS_ERROR: {e}")
        return False


def notify(name: str):
    if name not in NOTIFICATIONS:
        log(f"UNKNOWN: {name}")
        return False

    wav_file, fallback_sound, message = NOTIFICATIONS[name]
    log(f"FIRED: {name}")

    # wavファイルがある場合はもち子さん音声を再生
    if wav_file:
        wav_path = os.path.join(SOUNDS_DIR, wav_file)
        if os.path.exists(wav_path):
            if name == "approval":
                # approval: wavを即時再生（winsound直接、PowerShell不要）
                _play_wav(wav_path, async_mode=False)
                log(f"WAV: {name} ({wav_file})")
            else:
                # dev_done等: 同期再生
                ok = _play_wav(wav_path, async_mode=False)
                if ok:
                    log(f"WAV: {name} ({wav_file})")
                else:
                    log(f"WAV_FAIL: {name}, falling back to TTS")
                    _play_system_sound_and_tts(fallback_sound, message)

            log(f"DONE: {name}")
            return True
        else:
            log(f"WAV_MISSING: {wav_path}, falling back to TTS")

    # フォールバック: システム音 + TTS
    ok = _play_system_sound_and_tts(fallback_sound, message)
    log(f"{'DONE' if ok else 'FAIL'}: {name} (TTS fallback)")
    return ok


def test_all():
    """全通知を順番に実行"""
    log("=== TEST_ALL START ===")
    results = {}
    for name in NOTIFICATIONS:
        ok = notify(name)
        results[name] = "OK" if ok else "FAIL"
    log("=== TEST_ALL END ===")
    for name, status in results.items():
        print(f"  {name}: {status}")
    return all(v == "OK" for v in results.values())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python ops/notifications/notifier.py <notification_type>")
        print(f"Types: {', '.join(NOTIFICATIONS.keys())}, test_all")
        sys.exit(1)

    cmd = sys.argv[1]
    delay = "--no-delay" not in sys.argv

    defer = "--defer" in sys.argv
    is_child = "--_child" in sys.argv

    if cmd == "test_all":
        ok = test_all()
        sys.exit(0 if ok else 1)
    elif defer and not is_child:
        # Stop Hook用: pythonw.exe で子プロセスを起動して即終了
        # → Hook は即座に完了 → CEOにプロンプトが返る → 子プロセスが1秒後に音声再生
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        script = os.path.abspath(__file__)
        subprocess.Popen(
            [pythonw, script, cmd, "--_child"],
            creationflags=0x00000008 | 0x00000200,  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        )
        sys.exit(0)
    else:
        if is_child:
            time.sleep(5)
            log(f"DEFERRED: {cmd}")
        elif delay and HOOK_DELAY_SEC > 0:
            log(f"WAIT: {cmd} ({HOOK_DELAY_SEC}s delay)")
            time.sleep(HOOK_DELAY_SEC)
        ok = notify(cmd)
        sys.exit(0 if ok else 1)
