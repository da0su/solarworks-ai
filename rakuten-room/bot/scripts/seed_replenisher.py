#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seed_users.json BFS 補充スクリプト (2026-05-26 作成).

目的:
  447 seeds × 既フォロー 20,000+ で fresh pool 枯渇問題を解消。
  既存 seeds のフォロワーを BFS で収集して seed_users.json["all"] に追加し、
  毎日の FOLLOW 達成率を持続させる。

実行:
  python rakuten-room/bot/scripts/seed_replenisher.py
  python rakuten-room/bot/scripts/seed_replenisher.py --max-seeds 30 --target 1500

注意:
  - read-only operation (フォローはしない)
  - browser_manager は既存の chrome_profile_follow を流用
  - 既に follow 済 + 既に seed 登録済 はスキップ
  - playwright が必要・15-30 分かかる
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
import time
from pathlib import Path

# パス設定
SCRIPT_DIR = Path(__file__).resolve().parent
BOT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BOT_DIR))
sys.path.insert(0, str(BOT_DIR.parent.parent))  # for shared/

# 既存ロジックを再利用
from scripts.follow_via_seeds import (  # noqa: E402
    harvest_seed_followers,
    load_followed_history,
)
from executor.browser_manager import BrowserManager  # noqa: E402

SEED_PATH = BOT_DIR / "executor" / "seed_users.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("seed_replenisher")


def main() -> int:
    parser = argparse.ArgumentParser(description="seed_users.json BFS 補充")
    parser.add_argument("--max-seeds", type=int, default=30,
                        help="走査する既存 seed 数 (default 30)")
    parser.add_argument("--target", type=int, default=1500,
                        help="seed_users.json all の合計目標数 (default 1500)")
    parser.add_argument("--max-per-seed", type=int, default=100,
                        help="seed あたり最大 harvest 数 (default 100)")
    parser.add_argument("--dry-run", action="store_true",
                        help="保存せず結果ログのみ")
    args = parser.parse_args()

    if not SEED_PATH.exists():
        logger.error(f"seed_users.json not found: {SEED_PATH}")
        return 1

    seeds_data: dict = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    all_seeds: list[str] = list(seeds_data.get("all", []))
    initial_all_count = len(all_seeds)

    # 既存 seeds の dedup set (all + 各カテゴリ全部)
    existing: set[str] = set()
    for v in seeds_data.values():
        if isinstance(v, list):
            existing.update(v)
    logger.info(f"existing total: {len(existing)} unique seeds across all categories")

    # 既フォロー history (これも skip set に追加)
    already_followed = load_followed_history()
    logger.info(f"already_followed history: {len(already_followed)}")

    skip_set = existing | already_followed
    logger.info(f"skip_set total: {len(skip_set)}")

    # 走査する seed をランダム選択 (各カテゴリから均等)
    walk_seeds: list[str] = []
    for cat, lst in seeds_data.items():
        if not isinstance(lst, list) or not lst:
            continue
        per_cat = max(1, args.max_seeds // max(1, len(seeds_data)))
        sample = random.sample(lst, min(per_cat, len(lst)))
        walk_seeds.extend(sample)
    random.shuffle(walk_seeds)
    walk_seeds = walk_seeds[:args.max_seeds]
    logger.info(f"will walk {len(walk_seeds)} seeds across categories")

    # browser 起動 (chrome_profile_follow を流用)
    bm = BrowserManager(action="follow")
    try:
        bm.start()
        if not bm.check_login_status().get("logged_in"):
            logger.error("not logged in - aborting")
            return 2

        new_seeds: list[str] = []
        deadline = time.time() + 25 * 60  # 25 分 cap

        for i, seed in enumerate(walk_seeds, 1):
            if time.time() > deadline:
                logger.info(f"[deadline] 25 分 cap で打ち切り (i={i})")
                break
            if len(all_seeds) >= args.target:
                logger.info(f"[target] {args.target} 達成で打ち切り (current={len(all_seeds)})")
                break
            try:
                names = harvest_seed_followers(bm, seed, max_per_seed=args.max_per_seed)
            except Exception as e:
                logger.warning(f"[seed:{seed}] harvest failed: {e}")
                continue
            fresh = [n for n in names if n and n not in skip_set]
            if not fresh:
                logger.info(f"[seed:{seed}] harvested={len(names)} fresh=0 (all known)")
                continue
            # 追加
            for n in fresh:
                if n in skip_set:
                    continue
                all_seeds.append(n)
                skip_set.add(n)  # 重複防止
                new_seeds.append(n)
                if len(all_seeds) >= args.target:
                    break
            logger.info(
                f"[seed:{seed}] harvested={len(names)} fresh={len(fresh)} "
                f"all_total={len(all_seeds)} (+{len(all_seeds) - initial_all_count})"
            )

        logger.info(f"=== summary ===")
        logger.info(f"  initial all: {initial_all_count}")
        logger.info(f"  new fresh added: {len(new_seeds)}")
        logger.info(f"  final all total: {len(all_seeds)}")

        if args.dry_run:
            logger.info("[dry-run] not saving")
            return 0

        # backup → save
        if new_seeds:
            backup = SEED_PATH.with_suffix(
                f".{int(time.time())}.bak"
            )
            backup.write_text(SEED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info(f"backup written: {backup.name}")

            seeds_data["all"] = all_seeds
            SEED_PATH.write_text(
                json.dumps(seeds_data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            logger.info(f"saved: {SEED_PATH} (+{len(new_seeds)} new seeds)")
        else:
            logger.info("no new seeds - nothing to save")

        return 0
    finally:
        try:
            bm.stop()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
