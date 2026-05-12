"""follow_history_reader の unit test.

【再発防止】 新規 source 値追加時はここに test を追加.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestFollowHistoryReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json", mode="w", encoding="utf-8")
        sample = [
            {"user_name": "room_a", "followed_at": "2026-05-12T10:00:00", "source": "seed_followers"},
            {"user_name": "room_b", "followed_at": "2026-05-12T10:01:00", "source": "skip_discover"},
            {"user_name": "room_c", "followed_at": "2026-05-12T10:02:00", "source": "cli"},
            {"user_name": "room_d", "followed_at": "2026-05-11T23:59:00", "source": "seed_followers"},
            {"user_name": "room_e", "followed_at": "2026-05-12T10:03:00"},  # legacy no source
            {"user_name": "room_f", "followed_at": "2026-05-12T10:04:00", "source": "daily_plan"},
        ]
        json.dump(sample, self.tmp, ensure_ascii=False)
        self.tmp.close()

    def tearDown(self):
        Path(self.tmp.name).unlink(missing_ok=True)

    def test_count_real_follows_excludes_skip_discover(self):
        from shared import follow_history_reader as fh
        with patch.object(fh, "HIST_PATH", Path(self.tmp.name)):
            n = fh.count_real_follows_on("2026-05-12")
            # 5/12 real = a(seed), c(cli), e(legacy), f(daily_plan) = 4
            # b は skip_discover で除外
            self.assertEqual(n, 4, f"expected 4 real follows on 5/12, got {n}")

    def test_skip_discover_not_counted(self):
        from shared import follow_history_reader as fh
        with patch.object(fh, "HIST_PATH", Path(self.tmp.name)):
            dist = fh.count_by_source_on("2026-05-12")
            self.assertEqual(dist.get("skip_discover", 0), 1)
            # real だけだと 4
            real = fh.count_real_follows_on("2026-05-12")
            total = sum(dist.values())
            self.assertEqual(real, total - dist.get("skip_discover", 0))

    def test_date_filter(self):
        from shared import follow_history_reader as fh
        with patch.object(fh, "HIST_PATH", Path(self.tmp.name)):
            n_5_11 = fh.count_real_follows_on("2026-05-11")
            self.assertEqual(n_5_11, 1, "5/11 should be 1 (room_d)")

    def test_is_real_follow(self):
        from shared.follow_history_reader import is_real_follow
        self.assertTrue(is_real_follow({"source": "seed_followers"}))
        self.assertFalse(is_real_follow({"source": "skip_discover"}))
        self.assertTrue(is_real_follow({"source": "cli"}))
        self.assertTrue(is_real_follow({}))  # legacy no source
        self.assertFalse(is_real_follow("not a dict"))


if __name__ == "__main__":
    unittest.main()
