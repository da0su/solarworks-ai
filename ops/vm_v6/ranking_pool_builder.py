#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新POST方式: 8ジャンルのランキングTOP50を取り込み 400件プールを作成する.

CEO 2026-05-30 指示:
  - 基本ランキングからのみ抽出。8 URL × TOP50 = 400件。
  - 毎日朝7時に更新 (常に最新400件)。
  - 投稿文は元 content をそのまま流用 + 【ジャンル デイリーランキングN位】を挿入。

使い方 (VM 内):
  python ranking_pool_builder.py            # 400件プール作成のみ
  python ranking_pool_builder.py --post N   # 作成後ランダムN件投稿 (テスト)

実行場所: VM 内のみ (host_chrome_forbidden_rule)。
出力: \\vboxsvr\bot\data\ranking_pool.json (host repo bot/data)
"""
from __future__ import annotations
import sys, io, json, time, random, re, argparse, threading, subprocess
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"\\vboxsvr\vm_v6")   # runner package
sys.path.insert(0, r"\\vboxsvr\bot")     # executor / planner

from pathlib import Path
from datetime import datetime
from runner.browser_manager_v6 import BrowserManagerV6

# (category_lv1_id, ジャンル表示名)  CEO確定順 1..8
GENRES = [
    ("2800000100533426", "キッズ・ベビー・マタニティ"),
    ("2800000100371939", "レディースファッション"),
    ("2800000100804388", "インテリア・寝具・収納"),
    ("2800000558944399", "キッチン用品・食器・調理器具"),
    ("2800000216131238", "バッグ・小物・ブランド雑貨"),
    ("2800000215783421", "日用品雑貨・文房具・手芸"),
    ("2800000551167181", "スイーツ・お菓子"),
    ("2800000100227282", "食品"),
]
PER_GENRE = 50
POOL_PATH = Path(r"\\vboxsvr\bot\data\ranking_pool.json")
META_PATH = Path(r"\\vboxsvr\bot\data\ranking_pool_meta.json")
HIRATE_SRC = Path(r"\\vboxsvr\bot\data\high_rate_v2.json")  # CEo提供URLクロールの料率20%品
LOG_PATH = Path(r"\\vboxsvr\vm_data\_pool_build.log")
# 新ランキング方式 専用の投稿履歴 (凍結された post_history.json とは別管理)
# 1週間再投稿ルールの判定に使う。 {product_url: [posted_at ISO, ...]}
POST_LOG_PATH = Path(r"\\vboxsvr\bot\data\ranking_post_log.json")
REPOST_DAYS = 7          # この日数以内に投稿済みの商品は再投稿しない
DAILY_CAP = 50           # 1日あたり投稿上限 (スプシ B列=50 と一致)


def load_post_log() -> dict:
    try:
        if POST_LOG_PATH.exists():
            return json.loads(POST_LOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_post_log(plog: dict) -> None:
    tmp = POST_LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(plog, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(POST_LOG_PATH)


def _last_posted_at(plog: dict, key: str):
    """その商品の最新投稿時刻 (datetime) を返す。未投稿なら None。"""
    times = plog.get(key) or []
    if not times:
        return None
    try:
        return max(datetime.fromisoformat(t) for t in times)
    except Exception:
        return None


def is_repost_blocked(plog: dict, key: str, now: datetime) -> bool:
    """直近 REPOST_DAYS 日以内に投稿済みなら True (=投稿不可)。"""
    last = _last_posted_at(plog, key)
    if last is None:
        return False
    return (now - last).total_seconds() < REPOST_DAYS * 86400


def posted_today_count(plog: dict, now: datetime) -> int:
    today = now.date()
    cnt = 0
    for times in plog.values():
        for t in times:
            try:
                if datetime.fromisoformat(t).date() == today:
                    cnt += 1
            except Exception:
                pass
    return cnt


def record_post(plog: dict, key: str, now: datetime) -> None:
    plog.setdefault(key, []).append(now.isoformat())


def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# #3 AI高変換コピー (CEo承認GTM 2026-06-07): ママ訴求フック+信頼シグナル+検索ハッシュ。
# 価格断定は禁止 → 出力から価格表現を除去 (PRICE_PAT)。全文は維持(省略しない)。
# Codex指摘反映(2026-06-07): 価格断定を網羅(¥/￥, 桁区切り, 税込税別, 円程度/くらい/前後, %OFF, 万千円)
_PRICE_PAT = re.compile(
    r"[¥￥]\s*[0-9０-９][0-9０-９,，\.]*"
    r"|[0-9０-９][0-9０-９,，\.]*\s*(?:万|千)?\s*円(?:以下|以内|台|程度|くらい|前後|引|オフ|OFF)?"
    r"|ワンコイン|半額|税込|税抜|税別"
    r"|[0-9０-９]+\s*[%％]\s*(?:OFF|オフ|引)"
)
POST_MAXLEN = 480  # 文字数ガード(超過時は付加要素から削る・本文は切らない)


def price_clean(text: str) -> bool:
    """価格断定が残っていなければ True (投稿可)。最終コンプラゲート用。"""
    return not _PRICE_PAT.search(text or "")
GENRE_HOOK = {
    "キッズ・ベビー・マタニティ": "新米ママの「買ってよかった」",
    "レディースファッション": "安っぽく見えない高見え",
    "インテリア・寝具・収納": "暮らしが整うと心も軽い",
    "キッチン用品・食器・調理器具": "料理がもっとラクに",
    "バッグ・小物・ブランド雑貨": "毎日のおでかけが快適に",
    "日用品雑貨・文房具・手芸": "あると地味に助かる",
    "スイーツ・お菓子": "がんばった自分にご褒美",
    "食品": "家で楽しむお取り寄せ",
}
GENRE_TAGS = {
    "キッズ・ベビー・マタニティ": "#育児グッズ #ベビー用品 #新米ママ #赤ちゃんのいる暮らし",
    "レディースファッション": "#プチプラ高見え #ママコーデ #着回し",
    "インテリア・寝具・収納": "#インテリア #収納 #丁寧な暮らし",
    "キッチン用品・食器・調理器具": "#時短料理 #キッチン用品 #便利グッズ",
    "バッグ・小物・ブランド雑貨": "#ママバッグ #おでかけ #通園通勤",
    "日用品雑貨・文房具・手芸": "#日用品 #便利グッズ #リピ買い",
    "スイーツ・お菓子": "#お取り寄せスイーツ #ご褒美おやつ",
    "食品": "#お取り寄せグルメ #おうちごはん",
}


def build_review_text(content: str, genre: str, rank: int, review_count=0) -> str:
    """#3高変換: フック+ランキングタグ+信頼+本文(全文)+検索ハッシュ。価格除去。
    文字数ガード: 超過時は タグ→信頼→フック の順で削る。本文(content)は絶対に切らない。"""
    lines = (content or "").split("\n")
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    catch = lines[idx] if idx < len(lines) else ""
    rest = "\n".join(lines[idx + 1:])
    # Codex: rank が None(=ランキング非由来の料率20%品)は偽の「ランキングN位」を付けない
    tag = f"【{genre}　デイリーランキング{rank}位】" if rank else ""
    hook = GENRE_HOOK.get(genre, "")
    trust = "★レビュー高評価多数" if (str(review_count or 0).isdigit() and int(review_count or 0) >= 100) else ""
    tags = GENRE_TAGS.get(genre, "")

    def assemble(use_hook, use_trust, use_tags):
        parts = []
        if use_hook and hook:
            parts.append(hook)
        parts.append(catch)
        parts.append(tag)
        if use_trust and trust:
            parts.append(trust)
        if rest:
            parts.append(rest)
        if use_tags and tags:
            parts.append(tags)
        t = "\n".join(p for p in parts if p).strip()
        t = _PRICE_PAT.sub("", t)                    # 価格断定除去(安全弁)
        return re.sub(r"[ \t]+", " ", t).strip()

    for combo in [(1, 1, 1), (1, 1, 0), (1, 0, 0), (0, 0, 0)]:
        t = assemble(*combo)
        if len(t) <= POST_MAXLEN or combo == (0, 0, 0):
            return t   # (0,0,0)=本文+タグのみ。本文は切らない(全文維持)
    return assemble(0, 0, 0)


def load_hirate_pool() -> list:
    """CEo指定の料率20%プール(high_rate_v2.json)を ranking_pool スキーマで返す。
    2026-06-14 CEo指示「料率20%以上のみ投稿」の投稿源。法人限定/業務用は除外。"""
    try:
        if not HIRATE_SRC.exists():
            return []
        items = json.loads(HIRATE_SRC.read_text(encoding="utf-8")).get("items", [])
    except Exception:
        return []
    pool = []
    seen_urls = set()                            # Codex: 同一商品の多重投稿候補を防ぐ
    for i, it in enumerate(items):
        url = it.get("url")
        nm = it.get("name") or ""
        if not url or url in seen_urls or "法人限定" in nm or "業務用" in nm:
            continue
        seen_urls.add(url)
        pool.append({
            "genre": it.get("genre", ""), "category_lv1_id": None,
            "rank": None,                       # Codex: ランキング非由来→偽の順位を付けない
            "product_url": url, "name": nm[:120],
            "review_average": None, "review_count": 0,   # Codex: 偽のレビュー信頼シグナルを出さない
            "price": it.get("price"),
            "affiliate_rate": int((it.get("rate") or 0) * 10),
            "purchase_status": "1",
            "content": nm,                       # Codex: 「N%還元」は買い手誤認→商品名のみ
            "collect_id": None,
            "source_id": f"hirate_v2_{it.get('me_id')}_{it.get('item_id')}",
            "scraped_at": None, "_hirate": True,
        })
    return pool


def fetch_genre(page, cat_id: str, genre: str, want: int = PER_GENRE) -> list[dict]:
    """1ジャンルのランキングを offset ページングで want 件取得."""
    discover = f"https://room.rakuten.co.jp/discover/collectItemRank/{cat_id}"
    captured = []
    handler = lambda r: captured.append(r.url) if "/api/ranking/collect" in r.url else None
    page.on("request", handler)
    try:
        log(f"  goto {genre} ...")
        page.goto(discover, wait_until="domcontentloaded", timeout=30000)
        time.sleep(6)
    except Exception as e:
        log(f"  goto ERR {genre}: {type(e).__name__}: {str(e)[:80]}")
    finally:
        try: page.remove_listener("request", handler)
        except Exception: pass
    if not captured:
        log(f"  no api captured for {genre}")
        return []
    log(f"  api captured for {genre}: {captured[0][:70]}")
    base = captured[0]
    records = []
    seen = set()
    for offset in range(0, want, 10):
        u = re.sub(r"limit=\d+", "limit=10", base)
        u = re.sub(r"offset=\d+", f"offset={offset}", u)
        try:
            body = page.evaluate(
                "async (u) => { const r = await fetch(u, {credentials:'include'}); return await r.json(); }",
                u,
            )
        except Exception:
            break
        data = body.get("data", []) if isinstance(body, dict) else []
        if not data:
            break
        for entry in data:
            item = entry.get("item", {}) or {}
            product_url = item.get("url") or item.get("affiliate_url")
            cid = entry.get("id")
            if not product_url or cid in seen:
                continue
            seen.add(cid)
            rank = len(records) + 1
            records.append({
                "genre": genre,
                "category_lv1_id": cat_id,
                "rank": rank,
                "product_url": product_url,
                "name": item.get("name") or entry.get("name", ""),
                "review_average": item.get("review_average"),
                "review_count": item.get("review_count"),
                # 収益加重ポスティング用 (2026-06-07): 単価・料率を保存
                "price": item.get("price"),
                "affiliate_rate": item.get("affiliate_rate"),  # 0.1%単位 (例 40=4.0%)
                "purchase_status": item.get("purchase_status"),  # "1"=販売中のみ投稿
                "content": entry.get("content", ""),
                "collect_id": cid,
                "source_id": entry.get("source_id"),
                "scraped_at": datetime.now().isoformat(),
            })
            if len(records) >= want:
                break
        if len(records) >= want:
            break
    return records


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", type=int, default=0,
                    help="作成後ランダムN件投稿 (本番バッチ/テスト共用)")
    ap.add_argument("--daily-cap", type=int, default=DAILY_CAP,
                    help="1日の投稿上限 (これを超えない範囲で投稿)")
    ap.add_argument("--no-repost-filter", action="store_true",
                    help="1週間再投稿フィルタを無効化 (テスト用)")
    args = ap.parse_args()

    out = {"ts": datetime.now().isoformat(), "genres": {}}
    bm = BrowserManagerV6(action="post")
    pool = []
    try:
        bm.start()
        if not bm.is_logged_in():
            out["error"] = "not_logged_in"
            print(json.dumps(out, ensure_ascii=False)); return
        page = bm.page

        # --- Phase 1: プール構築 ---
        # CEo 2026-06-14「料率20%以上のみ投稿」: 投稿源は high_rate_v2(料率20%/¥3000+)のみ。
        # Codex指摘反映: 低料率のROOMランキングへフォールバックして投稿源を汚染しない。
        # hirate不在(クロール失敗等)時は既存pool(前回の20%品)を流用し、それも空なら投稿せず終了。
        hirate = load_hirate_pool()
        if hirate:
            pool = hirate
            out["source"] = "hirate_20pct"
            out["genres"] = {}
            for p in pool:
                out["genres"][p["genre"]] = out["genres"].get(p["genre"], 0) + 1
            POOL_PATH.write_text(json.dumps(pool, ensure_ascii=False, indent=2), encoding="utf-8")
            META_PATH.write_text(json.dumps({
                "created_at": datetime.now().isoformat(), "total": len(pool),
                "genres": out["genres"], "source": "hirate_20pct",
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[pool] 料率20%プール使用: {len(pool)}件 (ROOMランキングは使わない)", flush=True)
        else:
            # 低料率に汚染しない: 既存pool(前回の20%品)を流用
            try:
                pool = json.loads(POOL_PATH.read_text(encoding="utf-8"))
            except Exception:
                pool = []
            out["source"] = "existing_pool_fallback"
            print(f"[pool] hirate不在 → 既存pool {len(pool)}件を流用(低料率に汚染しない)", flush=True)
            if not pool:
                out["error"] = "no_hirate_pool"
                print(json.dumps(out, ensure_ascii=False)); return
        # --- コンセプト適合フィルタ (2026-08-01 CEO「まずはコンセプト」) ---
        # 正典: 09_INTELLIGENCE/room_growth/concept_and_longterm_plan.md
        # 「空くんと新米ママ」= 0-6歳を育てるママ向け。子どもの口に入る/肌に触れるもの。
        # 実測: プールの52%がレディースファッション/ダイエット健康で、これらは
        # 7月にクリックされても成約ゼロだった。供給の半分が「売れないもの」だった。
        # 料率20%の条件は維持したまま、さらにコンセプトで絞る。
        # concept_filter は同じ ops/vm_v6/ にある = VM からは W:\ 直下に見える。
        # (リポジトリ直下の ops/ は VM に共有されていないため ops.* は import 不可)
        try:
            from concept_filter import filter_items
            keep, drop = filter_items(pool)
            # スキーマ汚染を防ぐ: filter_items が付ける _concept_reason は
            # 診断用なので pool には残さない (下流が厳密スキーマを期待するため)
            keep = [{k: v for k, v in it.items() if k != "_concept_reason"} for it in keep]
            out["concept_filter"] = {
                "applied": True,
                "before": len(pool), "on_concept": len(keep), "dropped": len(drop),
            }
            if keep:
                pool = keep
                print(f"[concept] コンセプト適合 {len(keep)}件 / 除外 {len(drop)}件", flush=True)
            else:
                # 全滅する設定ミスで投稿を止めないための安全弁
                out["concept_filter"]["applied"] = False
                out["concept_filter"]["skipped"] = "on_concept=0"
                print("[concept] 適合0件のためフィルタを見送り (要確認)", flush=True)
        except Exception as e:
            # 未適用は「コンセプト外を投稿してしまう」状態。成功と誤認させない。
            out["concept_filter"] = {"applied": False,
                                     "error": f"{type(e).__name__}: {e}"[:120]}
            out["warnings"] = out.get("warnings", []) + ["concept_filter_not_applied"]
            print(f"[concept] ★未適用★ フィルタ読込失敗: {e}", flush=True)

        out["total"] = len(pool)

        # --- Phase 2: ランダム N件投稿 (テスト) ---
        if args.post > 0 and pool:
            from executor.post_executor import PostExecutor

            class CompatBM:
                def __init__(self, v6): self._v6 = v6
                @property
                def page(self): return self._v6.page
                def take_screenshot(self, label):
                    try:
                        d = Path(r"\\vboxsvr\vm_data\screenshots") / datetime.now().strftime("%Y-%m-%d")
                        d.mkdir(parents=True, exist_ok=True)
                        p = d / f"{datetime.now().strftime('%H%M%S')}_{label}.png"
                        self._v6.page.screenshot(path=str(p), full_page=False)
                        return p
                    except Exception:
                        return None
                def handle_session_upgrade(self, max_wait_sec: int = 15):
                    return self._v6.handle_session_upgrade(max_wait_sec=max_wait_sec)
                def save_session(self): pass

            pe = PostExecutor(CompatBM(bm))
            plog = load_post_log()
            now = datetime.now()

            # 1日上限を尊重: 既に今日投稿した分を差し引く
            already = posted_today_count(plog, now)
            remaining_cap = max(0, args.daily_cap - already)
            target = min(args.post, remaining_cap)
            out["posted_today_before"] = already
            out["daily_cap"] = args.daily_cap
            log(f"  posting: target={target} (req={args.post}, "
                f"today_already={already}, cap={args.daily_cap})")

            # 収益加重ポスティング (2026-06-07 CEo承認GTM): ランダム→「期待報酬順」。
            # 楽天上限: 1商品¥1,000(料率アップ除く)。∴ 単価×料率を¥1,000でcapし、
            # 同点は高料率(料率アップ)→人気で優先。bot検知回避にjitter。販売前は除外(ルール)。
            # #4 イベント・ブースト: SALE/5と0/ゴールデンタイム時は高料率/高単価をさらに上位へ。
            try:
                from trend_event_boost import current_event
                _ev = current_event(); _evmult = _ev.get("multiplier", 1.0)
            except Exception:
                _evmult = 1.0
            def _rev_score(e):
                try:
                    price = float(e.get("price") or 0)
                    rate = float(e.get("affiliate_rate") or 0) / 1000.0  # 40 -> 0.04
                except Exception:
                    price, rate = 0.0, 0.0
                # CEo 2026-06-14 ご指摘: 料率アップ品(_hirate)はpriceが空でも上限¥1,000満額を仮定
                # (実商品は高単価想定; 楽天の料率アップ品は通常¥3,000-¥10,000帯)
                if e.get("_hirate"):
                    capped = 1000.0      # 上限¥1,000満額を加算 (低料率の通常品より圧倒的優先)
                    # CMO(2026-06-14): 買いやすい価格帯(¥3,000-¥10,000)を優先・高額(低CVR)は後ろへ
                    if 3000 <= price <= 10000:
                        capped += 250
                    elif price > 30000:
                        capped -= 400
                else:
                    capped = min(price * rate, 1000.0)
                rate_bonus = rate * 2000.0                       # 高料率の優先度を倍に(料率6-20%が常に上位)
                try:
                    pop = min(float(e.get("review_count") or 0), 5000.0) / 5000.0 * 50.0
                except Exception:
                    pop = 0.0
                return (capped + rate_bonus + pop) * _evmult + random.uniform(0, 15)

            def _is_presale(e):
                ps = e.get("purchase_status")
                return ps is not None and str(ps) != "1"        # 販売中(1)以外除外。欠落は許容(旧プール互換)

            ranked = sorted(pool, key=_rev_score, reverse=True)
            # 販売前除外 → repost除外 → 2段選定。量(target)を絶対に枯らさない設計:
            #   pass1=ジャンル分散(cap) / pass2=不足分を無制限で補完。
            non_presale = [e for e in ranked if not _is_presale(e)]
            skipped_presale = len(ranked) - len(non_presale)
            postable = [e for e in non_presale
                        if args.no_repost_filter
                        or not is_repost_blocked(plog, e["product_url"], now)]
            skipped_repost = len(non_presale) - len(postable)
            genre_cap = max(2, -(-target // 5))                 # ジャンル偏り防止: 1ジャンル最大≒target/5
            chosen: list = []
            chosen_ids: set = set()
            _gu: dict = {}
            for e in postable:                                  # pass1: ジャンル分散
                if len(chosen) >= target:
                    break
                g = e.get("genre", "?")
                if _gu.get(g, 0) >= genre_cap:
                    continue
                _gu[g] = _gu.get(g, 0) + 1
                chosen.append(e); chosen_ids.add(id(e))
            if len(chosen) < target:                            # pass2: 量フォールバック(cap無視)
                for e in postable:
                    if len(chosen) >= target:
                        break
                    if id(e) in chosen_ids:
                        continue
                    chosen.append(e); chosen_ids.add(id(e))

            posts = []
            success = 0
            genre_used: dict = {}
            skipped_price = 0
            # 2026-07-21: 失敗補充。従来は chosen(=target件ちょうど) しか試さず、
            # price_detected / exec 失敗の分がそのまま欠損して 13件狙い→3〜5件が慢性化していた。
            # chosen の後ろに残り候補を連結し、成功が target に届くまで試行を続ける。
            _refill = [e for e in postable if id(e) not in chosen_ids]
            candidates = chosen + _refill
            attempts = 0
            browser_restarts = 0
            MAX_ATTEMPTS = target * 3          # 無限試行の安全弁

            # 2026-07-22: プロセスレベル watchdog。
            # Playwright のタイムアウトでも捕捉できない Chrome ゾンビ化 (CDP 呼び出しが
            # ソケットレベルで固まる) で pe.execute が無限ブロックし、VM 単スレッド HTTP
            # サーバを15分止める事象への対策 (実測 7/22: 7件投稿後にハング)。
            # 別スレッドが進捗を監視し、無進捗が続いたら chrome を強制 kill して
            # ブロックを解き、バッチをクリーン終了させる (サーバも解放)。
            # 時刻は monotonic (NTP 補正で逆行しない)。stall は 120s
            # (正常な投稿は ~15-30s。120s 無進捗は明確に異常)。
            # 注: taskkill /im chrome.exe は VM 上の全 Chrome を落とすため、
            # 同時刻に FOLLOW/LIKE が走っていれば巻き込む。ただし両者は
            # API 実数検証済みで「kill されても誤成功しない」ため許容トレードオフ
            # (詰まった POST の解除を優先)。
            _wd = {"last": time.monotonic(), "stop": False, "killed": False}
            WATCHDOG_STALL_SEC = 120

            def _watchdog():
                while not _wd["stop"]:
                    time.sleep(5)
                    if _wd["stop"]:
                        break
                    if time.monotonic() - _wd["last"] > WATCHDOG_STALL_SEC:
                        _wd["killed"] = True
                        log(f"[watchdog] {WATCHDOG_STALL_SEC}s 無進捗 → chrome 強制kill (ゾンビ解除)")
                        try:
                            subprocess.run(["taskkill", "/f", "/im", "chrome.exe"],
                                           capture_output=True, timeout=15)
                        except Exception as _ke:
                            log(f"[watchdog] taskkill 失敗: {_ke}")
                        break

            _wd_thread = threading.Thread(target=_watchdog, daemon=True)
            _wd_thread.start()

            for entry in candidates:
                if success >= target or attempts >= MAX_ATTEMPTS:
                    break
                if _wd["killed"]:
                    log("[watchdog] kill 済のためバッチ終了 (残りは次バッチで継続)")
                    break
                attempts += 1
                key = entry["product_url"]
                g = entry.get("genre", "?")
                rt = build_review_text(entry["content"], entry["genre"], entry["rank"],
                                       entry.get("review_count", 0))
                if not price_clean(rt):   # 最終コンプラゲート: 価格断定が残れば投稿しない(CEoルール)
                    skipped_price += 1
                    posts.append({"genre": entry["genre"], "rank": entry["rank"],
                                  "name": entry["name"][:40], "skipped": "price_detected"})
                    continue
                _wd["last"] = time.monotonic()   # watchdog に進捗を通知
                try:
                    res = pe.execute(entry["product_url"], rt)
                except Exception as e:
                    posts.append({"genre": entry["genre"], "rank": entry["rank"],
                                  "name": entry["name"][:40], "error": f"exc:{e}"[:80]})
                    continue
                finally:
                    _wd["last"] = time.monotonic()

                # 2026-07-23: ブラウザ死亡からの回復。
                # 実測 (7/23): 1件投稿後に Chrome が落ち、残り全試行が
                # "Target page, context or browser has been closed" で失敗
                # → 39試行で成功1件。閉じたブラウザに投げ続けても無駄なので、
                # 検知したら bm を作り直して同じ商品を1回だけ再試行する。
                err_txt = str(res.get("error") or "")
                if not res.get("success") and ("has been closed" in err_txt
                                               or "Target page" in err_txt):
                    log(f"[recover] ブラウザ死亡検知 → 再起動して再試行 ({browser_restarts+1}回目)")
                    try:
                        try:
                            bm.stop()
                        except Exception:
                            pass
                        bm = BrowserManagerV6(action="post")
                        bm.start()
                        pe = PostExecutor(CompatBM(bm))
                        browser_restarts += 1
                        _wd["last"] = time.monotonic()
                        res = pe.execute(entry["product_url"], rt)
                    except Exception as e2:
                        log(f"[recover] 再起動失敗: {e2}")
                        res = {"success": False, "error": f"recover_failed:{e2}"[:80]}
                    finally:
                        _wd["last"] = time.monotonic()

                ok = bool(res.get("success"))
                if ok:
                    success += 1
                    genre_used[g] = genre_used.get(g, 0) + 1   # ジャンル多様性カウント
                    record_post(plog, key, datetime.now())
                    save_post_log(plog)   # 1件毎に保存 (途中abort耐性)
                posts.append({
                    "genre": entry["genre"], "rank": entry["rank"],
                    "name": entry["name"][:40], "success": ok,
                    "error": res.get("error"), "len": len(rt),
                })
                print(f"[post] {entry['genre']} {entry['rank']}位 -> {ok}", flush=True)
                if success < target:
                    time.sleep(random.uniform(8, 15))
            _wd["stop"] = True   # watchdog 停止
            out["post_target"] = target
            out["post_request"] = args.post
            out["post_success"] = success
            out["post_attempts"] = attempts
            out["browser_restarts"] = browser_restarts
            out["watchdog_killed"] = _wd["killed"]
            out["skipped_repost"] = skipped_repost
            out["skipped_presale"] = skipped_presale
            out["genre_distribution"] = genre_used
            out["posts"] = posts
    except Exception as e:
        import traceback
        out["exception"] = f"{type(e).__name__}: {e}"
        out["tb"] = traceback.format_exc()[-800:]
    finally:
        try: bm.stop()
        except Exception: pass
        try:
            (Path(r"\\vboxsvr\vm_data") / "_pool_build_result.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        print("RESULT:" + json.dumps(out, ensure_ascii=False)[:1600])


if __name__ == "__main__":
    main()
