# -*- coding: utf-8 -*-
"""FOLLOWBACK verify_failed 診断 (VM 内実行専用).

プロフィールページ上の「フォローする」ボタンを全列挙し、
executor が .first で掴んでいるボタンが本当にプロフィール本人のものかを確認する。
仮説: おすすめユーザー等の別ボタンを掴んでいるため本人がフォローされない。

usage (VM内): python \\vboxsvr\vm_v6\_fb_diag.py <user_id>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(r"\\vboxsvr\vm_v6")))
from runner.browser_manager_v6 import BrowserManagerV6

USER = sys.argv[1] if len(sys.argv) > 1 else "room_f2a7306b80"
URL = f"https://room.rakuten.co.jp/{USER}/items"

SEL = ('button[aria-label="フォローする"], button:has-text("フォローする"), '
       'a:has-text("フォローする"), a.follow-button, button.follow-button')

bm = BrowserManagerV6(action="followback")
bm.start()
page = bm.page
print(f"=== {URL} ===", flush=True)
page.goto(URL, wait_until="domcontentloaded", timeout=25000)
page.wait_for_timeout(3000)
print("url_after:", page.url, flush=True)
print("logged_in:", bm.is_logged_in(), flush=True)

loc = page.locator(SEL)
n = loc.count()
print(f"\n[フォローする ボタン総数] {n}", flush=True)
for i in range(min(n, 12)):
    el = loc.nth(i)
    try:
        info = el.evaluate("""(e) => {
            const r = e.getBoundingClientRect();
            let ctx = [], p = e;
            for (let k = 0; k < 5 && p; k++) {
                p = p.parentElement;
                if (p && p.className && typeof p.className === 'string')
                    ctx.push(p.className.substring(0, 45));
            }
            return {
                tag: e.tagName, cls: (e.className || '').substring(0, 50),
                aria: e.getAttribute('aria-label'), href: e.getAttribute('href'),
                text: (e.innerText || '').trim().substring(0, 20),
                visible: r.width > 0 && r.height > 0,
                top: Math.round(r.top), left: Math.round(r.left),
                parents: ctx
            };
        }""")
        print(f"  [{i}] {info}", flush=True)
    except Exception as e:
        print(f"  [{i}] eval err {e}", flush=True)

# 本人プロフィール領域のボタンを特定できるか
print("\n[ページ主要見出し]", flush=True)
try:
    print("  title:", page.title()[:60], flush=True)
except Exception:
    pass

print("\n[.first をクリックして挙動確認]", flush=True)
try:
    first = loc.first
    before = first.evaluate("(e)=>e.innerText")
    first.click(timeout=5000)
    page.wait_for_timeout(4000)
    after_n = page.locator(SEL).count()
    print(f"  click OK: before_text={before!r} / クリック後のボタン総数={after_n} (元={n})", flush=True)
    page.goto(URL, wait_until="domcontentloaded", timeout=20000)
    page.wait_for_timeout(2500)
    renav_n = page.locator(SEL).count()
    already = page.locator('button:has-text("フォロー中"), a:has-text("フォロー中")').count()
    print(f"  再読込後: フォローする={renav_n} / フォロー中={already}", flush=True)
except Exception as e:
    print("  click err:", e, flush=True)

try:
    page.screenshot(path=r"\\vboxsvr\vm_data\_fb_diag.png", full_page=False)
    print("\nscreenshot -> vm_data\\_fb_diag.png", flush=True)
except Exception as e:
    print("ss err", e, flush=True)

bm.stop()
