#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""#27 日次投稿 → ジャンルコレクション自動追加 (incremental append)。

rebuilder との違い: ジャンル変更(=全リセット)をしない。既存商品を保ったまま
直近24hの新規投稿だけを該当ジャンルのコレクションに追加する。

フロー:
  1. 公開API /api/{USER}/collects で直近 LOOKBACK_H 時間の自分の投稿を取得
  2. source_id → ranking_pool.json (W:\\ranking_pool.json) でジャンル解決
  3. ジャンル毎の追加先コレクション = collection_rebuild_assignments.json の
     先頭 assignment (A01-A08 = 純ジャンルコレクション)
  4. コレクション既存 source_id を API で取得し重複除外 (既にあれば追加しない)
  5. 編集UI: 編集アイコン → showCategoryItems → スクロールロード →
     img_key 照合クリック (ジャンル変更はしない) → 完了 → createFinish
  6. API 再取得で新 source_id の反映を検証

安全装置:
  - 追加0件ならコレクションを触らない
  - コレクション上限 MAX_ITEMS(30) を超える追加はしない
  - クリック対象が1件も見つからなければ closeFreeCollection (保存しない)

使い方 (VM内): python collection_appender.py [--dry] [--hours 26]
"""
from __future__ import annotations
import sys, io, json, time, random, argparse
try:
    if sys.stdout and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, "W:\\")
from pathlib import Path
from datetime import datetime, timedelta
import urllib.request

ASSIGNMENTS_F = Path(r"X:\collection_rebuild_assignments.json")
STATE_V2_F    = Path(r"X:\collections_state_v2.json")
POOL_F        = Path(r"X:\high_rate_v2.json")
OUT_F         = Path(r"X:\_collection_appender_result.json")
SHOT_DIR      = Path(r"X:\screenshots") / datetime.now().strftime("%Y-%m-%d")
OWN_ROOM      = "room_e05d4d1c1e"
USER_ID       = "1000006606047125"
MAX_ITEMS     = 30    # コレクション上限 (これ以上は追加しない)
UA            = "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36"

try:
    OUT_F.write_text(json.dumps({"phase": "started"}, ensure_ascii=False), encoding="utf-8")
except Exception:
    pass

from runner.browser_manager_v6 import BrowserManagerV6

def _u(*cps): return "".join(chr(c) for c in cps)
TXT_DONE = _u(0x5b8c, 0x4e86)   # 完了

CLICK_NG  = """(needle)=>{const els=[...document.querySelectorAll('[ng-click]')];for(const el of els){const ng=el.getAttribute('ng-click')||'';if(ng.includes(needle)){const r=el.getBoundingClientRect();if(r.width>0&&r.height>0){el.click();return {ok:true,ng:ng};}}}return {ok:false};}"""
CLICK_TXT = """(txt)=>{const c=[];document.querySelectorAll('button,a,div,li,span,dl,dt').forEach(el=>{const t=(el.innerText||'').trim();if(!t)return;const r=el.getBoundingClientRect();if(r.width<=0||r.height<=0)return;if(t===txt)c.push({el,s:0,n:el.querySelectorAll('*').length});else if(t.includes(txt))c.push({el,s:1,n:el.querySelectorAll('*').length});});c.sort((a,b)=>a.s-b.s||a.n-b.n);if(!c.length)return false;c[0].el.click();return true;}"""
DL_COUNT  = "()=>document.querySelectorAll('.freeCollectionItems dl, .freeCollectionItemsCon dl').length"

SCROLL_LOAD_JS = """() => {
    const dls = document.querySelectorAll('.freeCollectionItems dl, .freeCollectionItemsCon dl');
    if (dls.length) { dls[dls.length-1].scrollIntoView(); }
    document.querySelectorAll('*').forEach(el => {
        const s = getComputedStyle(el);
        if (/(auto|scroll)/.test(s.overflowY) && el.scrollHeight > el.clientHeight) {
            el.scrollTop = el.scrollHeight;
        }
    });
    window.scrollTo(0, document.body.scrollHeight);
    return document.querySelectorAll('.freeCollectionItems dl, .freeCollectionItemsCon dl').length;
}"""

# rebuilder の PICK と同じ照合だが、選択済み(checked/selected class)は
# トグルOFFしてしまうため絶対にクリックしない。
PICK_NEW_JS = """(args) => {
    const keySet = new Set(args.target_keys);
    function normKey(src) {
        if (!src) return '';
        let s = src.replace(/^https?:\\/\\//, '').replace(/\\/$/, '').toLowerCase().split('?')[0];
        s = s.includes('/') ? s.slice(s.indexOf('/')+1) : s;
        if (s.startsWith('@0_mall/')) s = s.slice(8);
        return s;
    }
    const dls = document.querySelectorAll('.freeCollectionItems dl, .freeCollectionItemsCon dl');
    const picked = []; let skipped_checked = 0;
    for (const dl of dls) {
        const r = dl.getBoundingClientRect();
        if (r.width <= 0 || r.height <= 0) continue;
        const img = dl.querySelector('img');
        const src = img ? (img.getAttribute('src') || img.getAttribute('data-src') || img.src || '') : '';
        const key = normKey(src);
        if (!key || !keySet.has(key)) continue;
        const cls = (dl.className || '') + ' ' + [...dl.querySelectorAll('*')].map(e=>e.className||'').join(' ');
        if (/\\b(is-)?(checked|selected)\\b/i.test(cls)) { skipped_checked++; continue; }
        dl.click(); picked.push(key);
    }
    return {picked_n: picked.length, total_dls: dls.length, picked, skipped_checked};
}"""

def norm_key(src: str) -> str:
    """画像URL -> canonical key (host と @0_mall/ を除去したパス)。
    pool は thumbnail.image.rakuten.co.jp/@0_mall/{shop}/..、collects は
    tshop.r10s.jp/{shop}/.. とホストが違うが shop 以降のパスは一致する。"""
    if not src:
        return ""
    s = src.split("?")[0].lower().rstrip("/")
    for pre in ("https://", "http://"):
        if s.startswith(pre):
            s = s[len(pre):]
    s = s.split("/", 1)[1] if "/" in s else s
    if s.startswith("@0_mall/"):
        s = s[len("@0_mall/"):]
    return s

def api_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def coll_source_ids(coll_id):
    try:
        d = api_json(f"https://room.rakuten.co.jp/api/collection/{coll_id}/collects?limit=100")
        return [str(c.get("source_id")) for c in d.get("data", [])]
    except Exception as e:
        return {"err": str(e)[:80]}

def recent_posts(hours: float):
    """直近 hours の自分の投稿 [{source_id, img_key, created}]"""
    d = api_json(f"https://room.rakuten.co.jp/api/{USER_ID}/collects?limit=100")
    cutoff = datetime.now() - timedelta(hours=hours)
    out = []
    for c in d.get("data", []):
        try:
            ts = datetime.strptime(str(c.get("created_at", ""))[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if ts < cutoff:
            continue
        img = (c.get("image_top") or {}).get("url") or ""
        item_key = str((c.get("item") or {}).get("key") or "")  # "shop:itemcode"
        out.append({"source_id": str(c.get("source_id")), "img_key": norm_key(img),
                    "item_key": item_key.lower(), "created": str(c.get("created_at"))})
    return out

def url_to_item_key(url: str) -> str:
    """https://item.rakuten.co.jp/{shop}/{code}/ -> 'shop:code'"""
    try:
        parts = url.split("item.rakuten.co.jp/")[1].strip("/").split("/")
        return f"{parts[0]}:{parts[1]}".lower()
    except Exception:
        return ""

def shot(page, tag):
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        p = SHOT_DIR / f"append_{datetime.now().strftime('%H%M%S')}_{tag}.png"
        page.screenshot(path=str(p))
        return str(p)
    except Exception as e:
        return f"err:{e}"

def append_one(page, coll_id, name, img_keys, dry):
    rec = {"coll_id": coll_id, "name": name, "target_n": len(img_keys), "dry": dry}
    page.goto(f"https://room.rakuten.co.jp/{OWN_ROOM}/collections",
              wait_until="domcontentloaded", timeout=30000)
    time.sleep(2)
    page.goto(f"https://room.rakuten.co.jp/{OWN_ROOM}/collection/{coll_id}",
              wait_until="domcontentloaded", timeout=30000)
    time.sleep(4)
    page.evaluate("()=>{const el=document.querySelector('.common-edit,[class*=common-edit]'); if(el) el.click();}")
    time.sleep(3)

    # ジャンル変更はしない (既存商品を保つ)。直接 商品選択画面へ。
    page.evaluate(CLICK_NG, "showCategoryItems")
    time.sleep(3)
    counts = [page.evaluate(DL_COUNT)]
    for _ in range(15):
        n = page.evaluate(SCROLL_LOAD_JS)
        time.sleep(1.2)
        counts.append(n)
        if len(counts) >= 4 and counts[-1] == counts[-4]:
            break
    rec["dl_final"] = counts[-1]

    pick = page.evaluate(PICK_NEW_JS, {"target_keys": img_keys})
    rec["pick"] = pick
    picked_n = pick.get("picked_n", 0)
    rec["items_shot"] = shot(page, f"{coll_id}_items")
    time.sleep(1.5)
    page.evaluate(CLICK_TXT, TXT_DONE)
    time.sleep(2.5)

    if picked_n < 1 or dry:
        rec["no_save"] = "dry" if dry else "picked 0"
        page.evaluate(CLICK_NG, "closeFreeCollection")
        return rec

    rec["save"] = page.evaluate(CLICK_NG, "createFinish")
    time.sleep(5)
    rec["after_shot"] = shot(page, f"{coll_id}_after")
    time.sleep(3)
    sids = coll_source_ids(coll_id)
    rec["verified_n"] = len(sids) if isinstance(sids, list) else None
    rec["verified_sids"] = sids if isinstance(sids, list) else str(sids)
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--hours", type=float, default=26.0)
    args = ap.parse_args()

    out = {"ts": datetime.now().isoformat(), "dry": args.dry, "results": []}
    def save_out():
        try:
            OUT_F.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as we:
            print("write_err", we, flush=True)

    # 1) 直近投稿
    posts = recent_posts(args.hours)
    out["recent_posts"] = len(posts)
    print(f"recent posts ({args.hours}h): {len(posts)}", flush=True)
    if not posts:
        out["skip"] = "no recent posts"; save_out(); print("DONE no posts"); return

    # 入力ファイル不在/破損は結果ファイルに残して終了 (サイレント失敗防止)
    try:
        _ = POOL_F.stat(); _ = ASSIGNMENTS_F.stat(); _ = STATE_V2_F.stat()
    except Exception as e:
        out["error"] = f"input_missing:{e}"[:120]; save_out()
        print("ABORT", out["error"]); return

    # 2) img canonical key -> genre (high_rate_v2 の img で突合。
    #    source_id は無効値・URLスラッグと item.key は番号体系が別で突合不可のため画像で照合)
    pool = json.loads(POOL_F.read_text(encoding="utf-8"))
    pool_items = pool if isinstance(pool, list) else pool.get("items", [])
    key2genre = {}
    for x in pool_items:
        k = norm_key(x.get("img", "") or "")
        if k and x.get("genre"):
            key2genre[k] = x.get("genre")

    # 3) genre -> 現行コレクション。
    #    assignments の coll_id は 6/02 の全削除・再作成 (#34) 以前の旧IDで 400 になる。
    #    plan_id -> genre は assignments、plan_id -> 現行 coll_id は state_v2 で解決する。
    asgns = json.loads(ASSIGNMENTS_F.read_text(encoding="utf-8"))["assignments"]
    st_v2 = json.loads(STATE_V2_F.read_text(encoding="utf-8")).get("collections", {})
    genre2coll = {}
    for a in sorted(asgns, key=lambda x: x.get("plan_id", "")):
        cur = st_v2.get(a["plan_id"]) or {}
        if not cur.get("id"):
            continue
        genre2coll.setdefault(a["genre"], {"coll_id": cur["id"],
                                           "name": cur.get("name", a["name"])})

    # 4) ジャンル毎に新規追加分を決定
    plan = {}   # coll_id -> {"name","genre","img_keys":[],"sids":[]}
    unmapped = 0
    for p in posts:
        g = key2genre.get(p["img_key"])
        if not g or g not in genre2coll or not p["img_key"]:
            unmapped += 1
            continue
        c = genre2coll[g]
        plan.setdefault(c["coll_id"], {"name": c["name"], "genre": g,
                                       "img_keys": [], "sids": []})
        plan[c["coll_id"]]["img_keys"].append(p["img_key"])
        plan[c["coll_id"]]["sids"].append(p["source_id"])
    out["unmapped"] = unmapped

    # 既存重複除外 + 上限
    for cid in list(plan.keys()):
        existing = coll_source_ids(cid)
        if not isinstance(existing, list):
            plan[cid]["skip"] = f"api_err:{existing}"; continue
        eset = set(existing)
        keep = [(k, s) for k, s in zip(plan[cid]["img_keys"], plan[cid]["sids"]) if s not in eset]
        room = max(0, MAX_ITEMS - len(existing))
        keep = keep[:room]
        plan[cid]["img_keys"] = [k for k, _ in keep]
        plan[cid]["sids"]     = [s for _, s in keep]
        plan[cid]["existing_n"] = len(existing)
    todo = {cid: v for cid, v in plan.items() if v.get("img_keys") and "skip" not in v}
    out["plan"] = {cid: {"name": v["name"], "add_n": len(v["img_keys"]),
                         "existing_n": v.get("existing_n")} for cid, v in plan.items()}
    save_out()
    if not todo:
        out["skip"] = "nothing new to add"; save_out(); print("DONE nothing to add"); return

    # 5) 実行 (コレクション毎に独立 Chrome)
    for i, (cid, v) in enumerate(todo.items()):
        print(f"[{i+1}/{len(todo)}] {v['name'][:25]} add={len(v['img_keys'])}", flush=True)
        bm = BrowserManagerV6(action="post")
        try:
            bm.start()
            page = bm.page
            page.goto("https://room.rakuten.co.jp/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            if not bm.is_logged_in():
                bm.handle_session_upgrade(max_wait_sec=20)
            if not bm.is_logged_in():
                out["results"].append({"coll_id": cid, "error": "NOT logged in"})
                save_out()
                break
            rec = append_one(page, cid, v["name"], v["img_keys"], args.dry)
            rec["expected_new_sids"] = v["sids"]
            picked = (rec.get("pick") or {}).get("picked_n", 0)
            verified = rec.get("verified_sids")
            saved = (not args.dry) and (not rec.get("no_save"))  # 本番で保存を実行した
            if isinstance(verified, list):
                got = set(verified)
                rec["new_confirmed"] = sum(1 for s in v["sids"] if s in got)
                if saved and picked > 0 and rec["new_confirmed"] == 0:
                    rec["error"] = "save_not_reflected"   # 保存したのにAPI未反映
            elif saved:
                # 保存を実行したのに verify API が list を返さなかった時だけエラー化。
                # dry-run / 保存スキップ (no_save) はここに来ない。
                rec["error"] = rec.get("error") or "verify_unavailable"
            out["results"].append(rec)
            print(f"  -> picked={rec.get('pick',{}).get('picked_n')} "
                  f"confirmed={rec.get('new_confirmed','-')}", flush=True)
        except Exception as e:
            out["results"].append({"coll_id": cid, "error": f"exc:{e}"[:120]})
        finally:
            try: bm.stop()
            except Exception: pass
            save_out()
            time.sleep(random.uniform(3, 6))

    out["added_total"] = sum(r.get("new_confirmed") or 0 for r in out["results"])
    out["errors"] = [r.get("error") for r in out["results"] if r.get("error")]
    zero_picks = [] if args.dry else [
        r for r in out["results"] if (r.get("pick") or {}).get("picked_n", 0) == 0]
    if zero_picks:
        out["warn_zero_pick"] = len(zero_picks)  # ng-click/DOM 変化の兆候
    out["done"] = True
    save_out()
    print(f"DONE added={out['added_total']} errors={len(out['errors'])}", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        try:
            OUT_F.write_text(json.dumps(
                {"ts": datetime.now().isoformat(), "fatal": f"{e}"[:200],
                 "tb": traceback.format_exc()[-800:]}, ensure_ascii=False),
                encoding="utf-8")
        except Exception:
            pass
        raise
