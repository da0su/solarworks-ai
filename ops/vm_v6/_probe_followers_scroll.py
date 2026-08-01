#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FOLLOWBACK 真因調査: フォロワー一覧のスクロールで件数が増えるかを実測する。

背景 (2026-08-01): followback が 7/30 以降ゼロ。runner ログでは
scan_followers が scroll0 で js_users=19-20 しか取れず collected=0。
skip_set が36,562あるため「見えている20件は全員フォロー済み」で候補ゼロになる。
フォロワーは約18,000人いるはずなので、スクロールでの追加ロードが
効いていない疑いを実地で確認する。

出力: X:\_followers_scroll_probe.json
"""
from __future__ import annotations
import sys, io, json, time
try:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, "W:\\")
from pathlib import Path
from datetime import datetime

OUT = Path(r"X:\_followers_scroll_probe.json")
OWN_ROOM = "room_72f3a8cda6"

from runner.browser_manager_v6 import BrowserManagerV6

# scan と同等に room_xxx を集める簡易版 (件数推移の把握が目的)
JS_COUNT = """
() => {
  const re = /^room_[0-9a-z_.]{4,40}$/i;
  const ids = new Set();
  document.querySelectorAll('a[href]').forEach(a => {
    const h = a.getAttribute('href') || '';
    const m = h.match(/^\\/(room_[0-9a-z_.]+)\\//i);
    if (m && re.test(m[1])) ids.add(m[1]);
  });
  return {
    ids: ids.size,
    anchors: document.querySelectorAll('a[href]').length,
    bodyH: document.body.scrollHeight,
    innerH: window.innerHeight,
    scrollY: window.scrollY,
  };
}
"""

SCROLL_JS = """
() => {
  window.scrollBy(0, 3000);
  let containers = 0;
  document.querySelectorAll('*').forEach(c => {
    const s = getComputedStyle(c);
    if (/(auto|scroll)/.test(s.overflowY) && c.scrollHeight > c.clientHeight + 50) {
      c.scrollTop = c.scrollHeight; containers++;
    }
  });
  return containers;
}
"""


def probe(page, url, rec):
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    time.sleep(5)
    steps = []
    for i in range(12):
        st = page.evaluate(JS_COUNT)
        steps.append({"i": i, **st})
        print(f"  scroll{i}: ids={st['ids']} anchors={st['anchors']} "
              f"bodyH={st['bodyH']} scrollY={st['scrollY']}", flush=True)
        n = page.evaluate(SCROLL_JS)
        time.sleep(2.5)
        if i == 0:
            rec["scrollable_containers"] = n
    rec["steps"] = steps
    rec["ids_first"] = steps[0]["ids"]
    rec["ids_last"] = steps[-1]["ids"]
    rec["grew"] = steps[-1]["ids"] > steps[0]["ids"]
    return rec


def main():
    out = {"ts": datetime.now().isoformat(), "results": {}}
    bm = BrowserManagerV6(action="followback")
    try:
        bm.start()
        page = bm.page
        page.goto("https://room.rakuten.co.jp/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3)
        if not bm.is_logged_in():
            bm.handle_session_upgrade(max_wait_sec=20)
        out["logged_in"] = bm.is_logged_in()
        print(f"logged_in={out['logged_in']}", flush=True)

        for label, url in (
            ("my_followers", "https://room.rakuten.co.jp/my/followers"),
            ("own_followers", f"https://room.rakuten.co.jp/{OWN_ROOM}/followers"),
        ):
            print(f"=== {label} ===", flush=True)
            rec = {"url": url}
            try:
                probe(page, url, rec)
            except Exception as e:
                rec["error"] = f"{type(e).__name__}: {e}"[:200]
            out["results"][label] = rec
            OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        try: bm.stop()
        except Exception: pass
        OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
