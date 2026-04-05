"""オークションスキャナー: eBayオークション出品を探索し、入札候補を発見する
# CEO指示: シリーズ名フィルタ絶対禁止。全鑑定コイン対象。
# 検索クエリは「年号 + 国名 + 素材 + 鑑定会社(NGC/PCGS)」の4語。NGCとPCGSで2周する。
"""
import sys, os, json, time, random, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.supabase_client import get_client
from scripts.coin_matcher import extract_slab_key, is_same_coin

FX_RATE = 161

# シリーズ名ブラックリスト（検索クエリに含まれていたらエラー停止）
SERIES_BLACKLIST = [
    'morgan', 'eagle', 'sovereign', 'panda', 'britannia', 'maple',
    'liberty', 'krugerrand', 'peace', 'buffalo', 'kookaburra',
    'kangaroo', 'philharmonic', 'koala', 'angel', 'noble', 'una',
]

def validate_query(query):
    """検索クエリにシリーズ名が含まれていないかチェック。違反なら停止。"""
    words = query.lower().split()
    for word in words:
        if word in SERIES_BLACKLIST:
            raise ValueError(f"CEO指示違反: 検索クエリにシリーズ名 '{word}' が含まれています。")
    # 新ルール: 年号+国+素材+鑑定会社(NGC or PCGS)の4語。5語以上はNG。
    if len(words) > 4:
        raise ValueError(f"CEO指示違反: 検索クエリが5語以上 ({len(words)}語: '{query}')。")

COUNTRY_EN = {
    'アメリカ': 'america', 'イギリス': 'britain', 'カナダ': 'canada',
    'オーストラリア': 'australia', 'フランス': 'france', 'ドイツ': 'germany',
    'スイス': 'switzerland', '中国': 'china', 'オーストリア': 'austria',
    'メキシコ': 'mexico', '南アフリカ': 'south africa', 'ペルー': 'peru',
}

# ヒットしやすい順に並べる（CEO指示: 英国近年、ブリタニア、ソブリン、モルガン優先）
PRIORITY_COUNTRIES = ['britain', 'america', 'australia', 'canada', 'china', '', 'france', 'germany', 'switzerland']


def get_metal_en(title):
    t = title.lower()
    if '金貨' in title or 'gold' in t or 'ゴールド' in title:
        return 'gold'
    if '銀貨' in title or 'silver' in t or 'シルバー' in title:
        return 'silver'
    if 'プラチナ' in title or 'platinum' in t:
        return 'platinum'
    return ''


def main():
    client = get_client()
    data_dir = Path(__file__).parent.parent / 'data'

    # 進捗管理ファイル（3状態記録）
    progress_file = data_dir / 'auction_scanner_progress.json'
    keys_file = data_dir / 'fast_scanned_queries.json'  # 後方互換
    progress = {}  # {query_key: "completed_hit" | "completed_no_hit" | "failed"}
    if progress_file.exists():
        with open(progress_file, 'r', encoding='utf-8') as f:
            progress = json.load(f)
    seen_keys = set(k for k, v in progress.items() if v.startswith("completed"))

    def save_progress():
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        completed = [k for k, v in progress.items() if v.startswith("completed")]
        with open(keys_file, 'w', encoding='utf-8') as f:
            json.dump(sorted(completed), f, ensure_ascii=False, indent=2)
        with open(data_dir / 'candidates_auction.json', 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    # ヤフオク候補取得（シリーズフィルタなし、¥10万〜100万）
    resp = (client.table("market_transactions")
        .select("title, price_jpy, sold_date, url, grader, grade, year, country")
        .eq("source", "yahoo")
        .gte("sold_date", "2025-09-01")
        .gte("price_jpy", 100000)
        .lte("price_jpy", 1000000)
        .not_.is_("grader", "null")
        .order("sold_date", desc=True)
        .limit(1000)
        .execute())

    candidates = []
    for r in resp.data:
        t = r['title']
        info = extract_slab_key(t)
        year = info.get('year', '')
        if not year:
            continue

        gm = re.search(r'(MS|PF|PR)\s*(\d{1,2})', t, re.IGNORECASE)
        if not gm:
            continue
        gt = gm.group(1).upper()
        if gt == 'PR':
            gt = 'PF'
        grade_str = f'{gt}{gm.group(2)}'

        country = r.get('country', '') or ''
        country_e = COUNTRY_EN.get(country, '')
        metal = get_metal_en(t)
        grader = r.get('grader', '')

        key = f'{year}-{country_e}-{metal}-{grader}-{grade_str}'
        if key in seen_keys:
            continue
        # クエリレベルの重複排除（同じ年号+国+素材は1回だけ検索）
        query_key = f'{year}-{country_e}-{metal}'

        # CEO方式: 年号 + 国名 + 素材 だけで広く検索
        # グレード・鑑定会社はヒットした中からフィルタ
        query_parts = [str(year)]
        if country_e:
            query_parts.append(country_e)
        if metal:
            query_parts.append(metal)
        query = ' '.join(query_parts)

        candidates.append({
            'title': t, 'price': r['price_jpy'], 'date': r['sold_date'],
            'url': r['url'], 'year': year, 'country_en': country_e,
            'metal': metal, 'grader': grader, 'grade': grade_str,
            'key': key, 'query': query, 'info': info,
        })

    # ヒットしやすい順にソート（国優先度 → 年号新しい順）
    def sort_key(c):
        try:
            country_rank = PRIORITY_COUNTRIES.index(c['country_en'])
        except ValueError:
            country_rank = 99
        try:
            year_rank = -int(c['year'])
        except:
            year_rank = 0
        return (country_rank, year_rank)

    candidates.sort(key=sort_key)
    print(f'候補: {len(candidates)}件（ヒットしやすい順にソート済み）')
    sys.stdout.flush()

    # Playwright起動（クラッシュ時再起動対応）
    from playwright.sync_api import sync_playwright
    pw = None
    browser = None
    page = None

    def start_browser():
        nonlocal pw, browser, page
        try:
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except:
            pass
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=False,
            args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1280, "height": 800},
        )
        page = ctx.new_page()
        return page

    page = start_browser()

    # クエリレベル重複排除: completed済みはスキップ、failedは再試行
    unique_queries = []
    seen_qk = set()
    for cand in candidates:
        qk = f'{cand["year"]}-{cand["country_en"]}-{cand["metal"]}'
        if qk in seen_qk:
            continue
        seen_qk.add(qk)
        status = progress.get(qk, "")
        if status.startswith("completed"):
            continue
        unique_queries.append(cand)

    print(f'ユニーク検索クエリ: {len(unique_queries)}件（候補{len(candidates)}件から集約）')
    sys.stdout.flush()

    # Slackチェック関数（案E: 探索ループ内で毎回チェック）
    LATEST_MSG_FILE = Path.home() / '.slack_bridge_latest_msg'
    LAST_READ_FILE = Path.home() / '.slack_bridge_last_read'

    def check_slack():
        try:
            if not LATEST_MSG_FILE.exists():
                return
            msg = LATEST_MSG_FILE.read_text(encoding='utf-8').strip()
            last_read = ''
            if LAST_READ_FILE.exists():
                last_read = LAST_READ_FILE.read_text(encoding='utf-8').strip()
            if msg != last_read:
                LAST_READ_FILE.write_text(msg, encoding='utf-8')
                print(f'  [Slack新着] {msg[:80]}')
                sys.stdout.flush()
        except:
            pass

    results = []
    scanned = 0
    start_time = time.time()
    consecutive_errors = 0

    for cand in unique_queries:
        scanned += 1
        elapsed = time.time() - start_time
        rate = scanned / elapsed * 60 if elapsed > 0 else 0
        print(f'\n[{scanned}/{len(unique_queries)}] ({rate:.0f}件/分) {cand["query"]} | Y{cand["price"]:,}')

        # クエリバリデーション（CEO指示違反チェック）
        validate_query(cand["query"])
        sys.stdout.flush()

        # オークションのみ検索（LH_Auction=1）
        url = f'https://www.ebay.com/sch/i.html?_nkw={cand["query"].replace(" ", "+")}&LH_Auction=1'
        qk = f'{cand["year"]}-{cand["country_en"]}-{cand["metal"]}'
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=8000)
            page.wait_for_timeout(1500)
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            print(f'  ブラウザエラー ({consecutive_errors}回目): {str(e)[:50]}')
            progress[qk] = "failed"
            save_progress()
            if consecutive_errors >= 2:
                print(f'  ブラウザ再起動...')
                sys.stdout.flush()
                try:
                    page = start_browser()
                    consecutive_errors = 0
                    print(f'  再起動成功')
                except Exception as e2:
                    print(f'  再起動失敗: {str(e2)[:50]}。10秒待機')
                    time.sleep(10)
                    try:
                        page = start_browser()
                        consecutive_errors = 0
                        print(f'  2回目の再起動成功')
                    except:
                        print(f'  致命的エラー。終了')
                        break
            continue

        # URL一括取得（高速）
        item_ids = page.evaluate("""() => {
            const links = document.querySelectorAll('a[href*="/itm/"]');
            const seen = new Set();
            for (const link of links) {
                const m = (link.getAttribute('href') || '').match(/ebay\\.com\\/itm\\/(\\d+)/);
                if (m && m[1] !== '123456') seen.add(m[1]);
            }
            return [...seen].slice(0, 8);
        }""")

        if not item_ids:
            qk = f'{cand["year"]}-{cand["country_en"]}-{cand["metal"]}'
            progress[qk] = "completed_no_hit"
            save_progress()
            continue

        print(f'  {len(item_ids)}件ヒット → 上位5件巡回')
        sys.stdout.flush()

        # 上位5件巡回 — 全ヤフオク候補（同クエリに該当する全件）と照合
        # 同じクエリ（年号+国+素材）に該当するヤフオク候補を全て取得
        same_query_cands = [c for c in candidates if
            c['year'] == cand['year'] and c['country_en'] == cand['country_en']
            and c['metal'] == cand['metal']]

        for item_id in item_ids[:5]:
            item_url = f'https://www.ebay.com/itm/{item_id}'
            try:
                page.goto(item_url, wait_until="domcontentloaded", timeout=6000)
                page.wait_for_timeout(1000)
            except Exception:
                continue

            try:
                info = page.evaluate("""() => {
                    var title = document.title.replace(' | eBay', '').trim();
                    var body = document.body.innerText;
                    var usdMatch = body.match(/US\\s*\\$([\\d,]+\\.\\d{2})/);
                    var shipFree = /free\\s*shipping|送料無料/i.test(body);
                    var shipMatch = body.match(/US\\s*\\$([\\d.]+).*?(?:shipping|配送)/i);
                    var isAuction = /Place bid|入札する/i.test(body);
                    var isSold = /sold|ended|この出品は終了/i.test(body);
                    var bidMatch = body.match(/(\\d+)\\s*(?:bid|入札)/i);
                    var timeMatch = body.match(/(\\d+[dhms]\\s*(?:\\d+[dhms]\\s*)*)/i);
                    return {
                        title: title,
                        priceUsd: usdMatch ? parseFloat(usdMatch[1].replace(/,/g, '')) : null,
                        shipping: shipFree ? 0 : (shipMatch ? parseFloat(shipMatch[1]) : -1),
                        isAuction: isAuction,
                        isSold: isSold,
                        bidCount: bidMatch ? parseInt(bidMatch[1]) : 0,
                        timeLeft: timeMatch ? timeMatch[1] : '',
                    };
                }""")
            except Exception:
                continue

            if not info.get('priceUsd') or not info.get('title'):
                continue

            # NGC/PCGS含まれないものはスキップ
            if 'NGC' not in info['title'] and 'PCGS' not in info['title']:
                continue

            ebay_info = extract_slab_key(info['title'])

            # 全ヤフオク候補と照合
            for yc in same_query_cands:
                matched, reason = is_same_coin(ebay_info, yc['info'])
                if not matched:
                    continue

                ship_jpy = int(info['shipping'] * FX_RATE) if info['shipping'] > 0 else 0
                cost = int(info['priceUsd'] * FX_RATE * 1.1) + ship_jpy + 2750
                net = int(yc['price'] * 0.9)
                profit = net - cost
                pct = round(profit / cost * 100, 1) if cost > 0 else 0

                # Sold/Ended除外
                if info.get('isSold'):
                    continue

                # 最大入札額計算
                yahoo_net = int(yc['price'] * 0.9)
                max_cost = int(yahoo_net / 1.2)  # 利益20%確保
                ship_jpy_est = ship_jpy if ship_jpy > 0 else 0
                max_bid_usd = int((max_cost - ship_jpy_est - 2750) / (FX_RATE * 1.1))

                auc = "[AUC]" if info['isAuction'] else "[BIN]"
                print(f'  >>> {auc} +{pct}% | 現在${info["priceUsd"]} | 上限${max_bid_usd} | {info["title"][:45]}')
                print(f'      vs Y{yc["price"]:,} | 入札{info.get("bidCount",0)}件 | 残{info.get("timeLeft","")} | {yc["title"][:45]}')
                sys.stdout.flush()

                results.append({
                    'key': yc['key'],
                    'ebay_url': item_url,
                    'ebay_title': info['title'],
                    'ebay_price_usd': info['priceUsd'],
                    'ebay_shipping': info['shipping'],
                    'is_auction': info['isAuction'],
                    'bid_count': info.get('bidCount', 0),
                    'time_left': info.get('timeLeft', ''),
                    'max_bid_usd': max_bid_usd,
                    'yahoo_url': yc['url'],
                    'yahoo_title': yc['title'],
                    'yahoo_price': yc['price'],
                    'cost': cost,
                    'profit': profit,
                    'pct': pct,
                    'match_reason': reason,
                })
                break

            # ヒットありの場合のみ少し待機
            time.sleep(random.uniform(0.5, 1))

        # クエリ完了記録（eBay検索+巡回が実際に実行された場合のみ）
        has_match = any(r.get('ebay_url', '').endswith(item_id) for r in results for item_id in item_ids[:5])
        progress[qk] = "completed_hit" if has_match else "completed_no_hit"
        save_progress()

    try:
        browser.close()
        pw.stop()
    except:
        pass

    elapsed_total = time.time() - start_time
    profitable = [r for r in results if r['pct'] > 0]
    completed = sum(1 for v in progress.values() if v.startswith("completed"))
    failed = sum(1 for v in progress.values() if v == "failed")
    print(f'\n{"="*60}')
    print(f'処理: {scanned}件 / {elapsed_total:.0f}秒 ({scanned/elapsed_total*60:.1f}件/分)')
    print(f'完了: {completed}件 / 失敗: {failed}件')
    print(f'マッチ: {len(results)}件 / 利益あり: {len(profitable)}件')

    for r in sorted(results, key=lambda x: x['pct'], reverse=True):
        auc = "[AUC]" if r['is_auction'] else "[BIN]"
        print(f'\n  {auc} +{r["pct"]}% | Y{r["profit"]:,}')
        print(f'  eBay: ${r["ebay_price_usd"]:,.2f} | {r["ebay_title"][:55]}')
        print(f'  Yahoo: Y{r["yahoo_price"]:,} | {r["yahoo_title"][:55]}')
        print(f'  {r["ebay_url"]}')
        print(f'  {r["yahoo_url"]}')

    # 通知
    try:
        import subprocess
        notifier = str(Path(__file__).parent.parent.parent / 'ops' / 'notifications' / 'notifier.py')
        subprocess.run(['python', notifier, 'dev_done'], timeout=15)
    except Exception:
        pass


if __name__ == '__main__':
    main()
