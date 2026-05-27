#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VM v6 SEED REPLENISH executor: seed_users.json を VM 内で BFS 拡張.

2026-05-27 CEO 指示「HOST Chrome NG・VM 内で完結」を受けて作成.
旧 rakuten-room/bot/scripts/seed_replenisher.py (HOST 用) を VM-internal 化.

仕様:
  - 既存 seed_users.json (\\vboxsvr\bot\executor\seed_users.json) を読む
  - 既存 seeds の followers ページを harvest して新規候補を集める
  - 既フォロー (follow_history.json) + 既存 seeds と重複除外
  - VM 内 chrome_profile_follow を使用 (BrowserManagerV6)
  - .bak backup → atomic write
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

from .shared_logic import HeartbeatPusher, SessionLogger, BASE_DIR, emergency_disk_cleanup_once
from .browser_manager_v6 import BrowserManagerV6

try:
    emergency_disk_cleanup_once()
except Exception as _e:
    print(f"[disk_cleanup_seed_replenish] err: {_e}")

# VM では \\vboxsvr\bot 経由でアクセス
HOST_BOT_DIR = Path(r"\\vboxsvr\bot")
SEED_USERS_PATH = HOST_BOT_DIR / "executor" / "seed_users.json"
HISTORY_PATH = HOST_BOT_DIR / "data" / "follow_history.json"

_ROOM_ID_RE = re.compile(r'^room_[0-9a-f]{8,40}$')
_CUSTOM_USERNAME_RE = re.compile(r'^[a-z0-9_.\-]{3,40}$')


def _load_history() -> set:
    """既フォロー history から user_id/name の set を構築."""
    if not HISTORY_PATH.exists():
        return set()
    try:
        h = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        s = set()
        for e in h:
            if isinstance(e, dict):
                if e.get("user_id"):
                    s.add(e["user_id"])
                if e.get("user_name"):
                    s.add(e["user_name"])
        return s
    except Exception:
        return set()


def _harvest_followers(page, seed: str, max_per_seed: int = 100,
                       log: SessionLogger = None) -> list[str]:
    """seed の followers ページから user_id を収集."""
    url = f"https://room.rakuten.co.jp/{seed}/followers"
    collected = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(3000)
        if "grp01.id.rakuten.co.jp" in page.url or "/nid/" in page.url:
            if log:
                log.log(f"[harvest:{seed}] login redirect → skip")
            return []
        # スクロールで lazy load 発火
        for _ in range(5):
            try:
                page.evaluate("window.scrollBy(0, 1500)")
                page.wait_for_timeout(800)
            except Exception:
                break
        anchors = page.query_selector_all('a[href*="/"]')
        seen = set()
        for a in anchors:
            try:
                href = (a.get_attribute("href") or "").strip()
            except Exception:
                continue
            if not href.startswith("/"):
                continue
            parts = href.lstrip("/").split("/")
            if not parts:
                continue
            seg = parts[0].split("?")[0]
            if not seg or seg in {"items", "my", "discover", "items", "search",
                                  "timeline", "ranking", "register", "login",
                                  "categories", "settings", "campaigns"}:
                continue
            if not (_ROOM_ID_RE.match(seg) or _CUSTOM_USERNAME_RE.match(seg)):
                continue
            if seg in seen:
                continue
            seen.add(seg)
            collected.append(seg)
            if len(collected) >= max_per_seed:
                break
        if log:
            log.log(f"[harvest:{seed}] collected {len(collected)}")
    except Exception as e:
        if log:
            log.log(f"[harvest:{seed}] error: {e}")
    return collected


def run_seed_replenish(limit: int = 1500, hb: HeartbeatPusher = None,
                       log: SessionLogger = None) -> dict:
    """seed_users.json BFS 補充. limit は 'all' カテゴリの目標総数."""
    if hb is None:
        hb = HeartbeatPusher("seed_replenish")
    if log is None:
        log = SessionLogger("seed_replenish")

    log.log(f"=== SEED REPLENISH executor v6 start: target={limit} ===")
    hb.write(phase="startup", force=True)

    result = {"new_seeds": 0, "final_total": 0, "stop_reason": "unknown"}

    if not SEED_USERS_PATH.exists():
        log.log(f"[ABORT] seed_users.json not found at {SEED_USERS_PATH}")
        result["stop_reason"] = "seed_file_missing"
        return result

    try:
        seeds_data: dict = json.loads(SEED_USERS_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        log.log(f"[ABORT] seed_users.json read error: {e}")
        result["stop_reason"] = "seed_file_read_error"
        return result

    all_seeds: list[str] = list(seeds_data.get("all", []))
    initial = len(all_seeds)

    # dedup set: all categories + history
    existing: set[str] = set()
    for v in seeds_data.values():
        if isinstance(v, list):
            existing.update(v)
    already_followed = _load_history()
    skip_set = existing | already_followed
    log.log(f"existing seeds={len(existing)} history={len(already_followed)} skip_total={len(skip_set)}")

    # walk seeds (各カテゴリから均等にサンプル)
    walk_seeds: list[str] = []
    for cat, lst in seeds_data.items():
        if not isinstance(lst, list) or not lst:
            continue
        sample = random.sample(lst, min(5, len(lst)))
        walk_seeds.extend(sample)
    random.shuffle(walk_seeds)
    walk_seeds = walk_seeds[:30]
    log.log(f"walking {len(walk_seeds)} seeds")

    bm = BrowserManagerV6(action="follow")
    try:
        bm.start()
        hb.write(phase="login_check")
        if not bm.is_logged_in():
            log.log("[ABORT] not logged in")
            result["stop_reason"] = "login_expired"
            return result
        page = bm.page

        deadline = time.time() + 25 * 60  # 25 分
        new_collected = []
        for i, seed in enumerate(walk_seeds, 1):
            if time.time() > deadline:
                log.log(f"[deadline] 25 min cap (i={i})")
                break
            if len(all_seeds) >= limit:
                log.log(f"[target] {limit} reached")
                break
            hb.write(phase=f"harvest_{i}", current=i, total=len(walk_seeds))
            names = _harvest_followers(page, seed, max_per_seed=100, log=log)
            fresh = [n for n in names if n and n not in skip_set]
            for n in fresh:
                if n in skip_set:
                    continue
                all_seeds.append(n)
                skip_set.add(n)
                new_collected.append(n)
                if len(all_seeds) >= limit:
                    break
            if fresh:
                log.log(f"[seed:{seed}] +{len(fresh)} fresh / all_total={len(all_seeds)}")

        result["new_seeds"] = len(new_collected)
        result["final_total"] = len(all_seeds)

        if new_collected:
            # backup
            backup = SEED_USERS_PATH.with_suffix(f".{int(time.time())}.bak")
            try:
                backup.write_text(SEED_USERS_PATH.read_text(encoding="utf-8"),
                                  encoding="utf-8")
                log.log(f"backup: {backup.name}")
            except Exception as e:
                log.log(f"backup fail (continuing): {e}")
            # atomic write
            seeds_data["all"] = all_seeds
            tmp = SEED_USERS_PATH.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(seeds_data, ensure_ascii=False, indent=2),
                               encoding="utf-8")
                tmp.replace(SEED_USERS_PATH)
                log.log(f"saved: +{len(new_collected)} new seeds (total={len(all_seeds)})")
                result["stop_reason"] = "completed"
            except Exception as e:
                log.log(f"[ERROR] save fail: {e}")
                result["stop_reason"] = f"save_error: {e}"
        else:
            log.log("no new seeds collected")
            result["stop_reason"] = "no_new_seeds"

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        log.log(f"[ERROR] seed replenish: {e}\n{tb[:800]}")
        result["stop_reason"] = f"executor_error: {type(e).__name__}: {e}"
    finally:
        try:
            bm.stop()
        except Exception:
            pass
        hb.write(phase="shutdown",
                 new_seeds=result["new_seeds"],
                 final_total=result["final_total"],
                 force=True)
        log.log(f"=== SEED REPLENISH executor v6 end: {result} ===")

    return result
