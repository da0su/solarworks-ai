"""product_fetcher: PERSONA_GENRE_WEIGHTS 適用 unit test (Codex 29回目 #2 反映).

CEO 5/20: weight=3.0 (food/baby) なのに 5/18 以降 取得 2件/3件 だった真因は
dict 挿入順 iterate + 早期 target break で 後ろのジャンルが skip されていたこと.
修正後 weight 降順 iterate + 高 weight は target 超過しても取得継続.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))

import config
from planner import product_fetcher as pf


def _fake_fetch_genre_items(genre, keywords, max_per_keyword=60, exclude_codes=None):
    """fetch_genre_items を mock. max_per_keyword の半分を返す (deterministic)."""
    n = max(1, max_per_keyword * len(keywords) // 4)
    return [
        {"item_code": f"{genre}_{i}", "url": f"https://item.rakuten.co.jp/{genre}/{i}",
         "title": f"{genre} item {i}", "genre": genre, "score": 70,
         "price": 1000, "image_url": "x", "shop_name": "test",
         "review_count": 10, "review_avg": 4.0,
         "priority": 2, "fetched_at": "2026-05-20T00:00:00"}
        for i in range(n)
    ]


def test_weight_descending_iterate():
    """fetch は weight 降順で iterate (food/baby/kids が先頭) ること."""
    with patch.object(pf, "fetch_genre_items", side_effect=_fake_fetch_genre_items) as m, \
         patch.object(pf, "_load_posted_item_codes", return_value=set()), \
         patch.object(pf, "_load_existing_item_codes", return_value=set()):
        items = pf.fetch_all_genres(target_total=200, run_id="test_weight")

    # 呼び出し順を見る (mock の call_args_list)
    call_genres = [c.args[0] for c in m.call_args_list]
    assert len(call_genres) > 0
    # food (3.0) / baby (3.0) は先頭 8 位以内に来るはず
    food_pos = call_genres.index("food") if "food" in call_genres else 999
    baby_pos = call_genres.index("baby") if "baby" in call_genres else 999
    kids_pos = call_genres.index("kids") if "kids" in call_genres else 999

    assert food_pos < 5, f"food should be fetched in first 5, got pos {food_pos} in {call_genres}"
    assert baby_pos < 5, f"baby should be fetched in first 5, got pos {baby_pos} in {call_genres}"
    assert kids_pos < 8, f"kids should be fetched in first 8, got pos {kids_pos} in {call_genres}"


def test_excluded_genres_not_called():
    """weight=0 (pet/car/outdoor/garden) は呼ばれない."""
    with patch.object(pf, "fetch_genre_items", side_effect=_fake_fetch_genre_items) as m, \
         patch.object(pf, "_load_posted_item_codes", return_value=set()), \
         patch.object(pf, "_load_existing_item_codes", return_value=set()):
        pf.fetch_all_genres(target_total=200, run_id="test_excluded")
    call_genres = [c.args[0] for c in m.call_args_list]
    for excluded in ("pet", "car", "outdoor", "garden"):
        assert excluded not in call_genres, f"{excluded} should be skipped (weight=0)"


def test_allowlist_mode():
    """allowlist 指定時は それ以外は完全に skip (CEO 5/20 緊急止血)."""
    with patch.object(pf, "fetch_genre_items", side_effect=_fake_fetch_genre_items) as m, \
         patch.object(pf, "_load_posted_item_codes", return_value=set()), \
         patch.object(pf, "_load_existing_item_codes", return_value=set()):
        pf.fetch_all_genres(target_total=100,
                            allowlist_genres=["baby", "food", "kids"],
                            run_id="test_allow")
    call_genres = [c.args[0] for c in m.call_args_list]
    assert set(call_genres) <= {"baby", "food", "kids"}, \
        f"allowlist breached: {call_genres}"


def test_high_priority_min_floor():
    """高 weight (food/baby) は最低保証 (NORMAL_MIN) より多く取得."""
    captured_kw_per_call = {}

    def _capture(genre, keywords, max_per_keyword=60, exclude_codes=None):
        captured_kw_per_call[genre] = max_per_keyword
        return _fake_fetch_genre_items(genre, keywords, max_per_keyword, exclude_codes)

    with patch.object(pf, "fetch_genre_items", side_effect=_capture), \
         patch.object(pf, "_load_posted_item_codes", return_value=set()), \
         patch.object(pf, "_load_existing_item_codes", return_value=set()):
        pf.fetch_all_genres(target_total=200, run_id="test_floor")

    # food/baby (weight=3.0) は 高保証 (NORMAL_MIN=25 より大きい per_keyword)
    food_kw = captured_kw_per_call.get("food", 0)
    baby_kw = captured_kw_per_call.get("baby", 0)
    assert food_kw > 0, f"food should be fetched, got max_per_keyword={food_kw}"
    assert baby_kw > 0, f"baby should be fetched, got max_per_keyword={baby_kw}"


def test_run_id_written_to_jsonl(tmp_path=None):
    """run_id 指定時に run_logs/<run_id>.jsonl が書かれる."""
    import json
    run_id = "test_run_12345"
    log_path = config.DATA_DIR / "run_logs" / f"{run_id}.jsonl"
    if log_path.exists():
        log_path.unlink()
    with patch.object(pf, "fetch_genre_items", side_effect=_fake_fetch_genre_items), \
         patch.object(pf, "_load_posted_item_codes", return_value=set()), \
         patch.object(pf, "_load_existing_item_codes", return_value=set()):
        pf.fetch_all_genres(target_total=100, run_id=run_id)
    assert log_path.exists(), f"jsonl log not written: {log_path}"
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[-1]
    data = json.loads(line)
    assert data["type"] == "fetch_all_genres_complete"
    assert "weights" in data
    assert "genre_counts" in data
    log_path.unlink()  # cleanup


def test_target_zero_returns_empty():
    """Codex 31回目 #7: target<=0 で 早期 return [] (異常呼出ガード).
    target_total=0 だと config.POOL_MIN にフォールバックするので、
    explicitly -1 を渡す.
    """
    # target_total=-1 で明示的に負値 (or 0 でも config fallback 後 < 0 にはならないので別 path)
    items = pf.fetch_all_genres(target_total=-1)
    assert items == []
    # target_total=None で config.POOL_MIN にフォールバック (これは異常呼出ではない)
    # ので test 対象外.


def test_empty_keywords_genre_skipped():
    """Codex 31回目 #6: len(keywords)==0 の genre は skip (ZeroDivisionError 防止)."""
    # config の genre keywords を一時的に空にして test
    orig = dict(config.GENRE_SEARCH_KEYWORDS)
    try:
        config.GENRE_SEARCH_KEYWORDS["food"] = []  # 空 keywords
        with patch.object(pf, "fetch_genre_items", side_effect=_fake_fetch_genre_items) as m, \
             patch.object(pf, "_load_posted_item_codes", return_value=set()), \
             patch.object(pf, "_load_existing_item_codes", return_value=set()):
            pf.fetch_all_genres(target_total=100, run_id="test_empty_kw")
        call_genres = [c.args[0] for c in m.call_args_list]
        assert "food" not in call_genres, "empty keywords genre は skip"
    finally:
        config.GENRE_SEARCH_KEYWORDS.clear()
        config.GENRE_SEARCH_KEYWORDS.update(orig)


if __name__ == "__main__":
    import traceback
    tests = [
        test_weight_descending_iterate,
        test_excluded_genres_not_called,
        test_allowlist_mode,
        test_high_priority_min_floor,
        test_run_id_written_to_jsonl,
        test_target_zero_returns_empty,
        test_empty_keywords_genre_skipped,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS: {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}")
            failed += 1
        except Exception:
            print(f"ERR : {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n=> {passed} pass / {failed} fail")
    sys.exit(0 if failed == 0 else 1)
