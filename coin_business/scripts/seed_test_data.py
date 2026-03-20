"""seed_test_data.py - テストデータ10件一括投入"""
import json, os, sys, urllib.request
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
env = {}
for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
    if line.strip() and "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

API_KEY = env["AIRTABLE_API_KEY"]
BASE_ID = env["AIRTABLE_BASE_ID"]

CM = "tblFLv5Cq9zpOmTc6"
YS = "tblVvoarXhnFTT9BE"
SR = "tblrWMUHlBIn9WWce"
LS = "tbl8Ajg41oN2RilAb"

CM_F = {"coin_id":"fld0sDWG41KHJ99dT","country":"fldUTtgUn5f1xvQ3q","year":"fldiRk8ituRjLe823","denom":"fldOsGYYzfvC6TLhw","material":"fldxHuQCntGMJn8I8","weight":"fldRm3Fa5qR3OvQ0b","diameter":"fldG8c94HOvW6C6cD","grader":"fldtwinYCT6PKXSCV","grade":"fldYbOvd5gy0esKf6","tags":"fldxgehQqeTi4KOmd"}
YS_F = {"record_id":"fldYdrJ8s21xJJB28","coin_id":"fldrZ2xV58vVRdtvn","price":"fld3twaf0Cu7WJGpc","date":"fldeloKTYGKRuUtmB","url":"fldvEd5iq7qWUp951","days":"fldK78n17CkiXOsRe","note":"fldRNFUZ0uO8mvfgw"}
SR_F = {"record_id":"fldjuOpEIlrOpz3ZD","coin_id":"fldi5z5wbYCGBY0yK","purchase":"fld5AKwuijhn4eZcY","currency":"fldznF8VCjEAhOe9S","shipping":"fldGiLcg6FFivVJ7u","tariff":"fldNfpkCVOxdZKnKN","total":"fldQeAmTJNS5oYfNY","site":"fldgQNcdtlbEr7z8l","url":"fldVaoVE9wS4K3Lwj"}
LS_F = {"record_id":"fldWcJTQkErG5QrQZ","coin_id":"fldiPcBQ3gWGnxEEQ","title":"flduOunm1luLQGySA","desc":"fldiBx87dvlh4k97i","start_price":"fldKtmDPFaIm4FTV3","buynow":"fldRVMv3fCs2up8Od","sold_price":"fldqGNAJuEdC6Oa5Q","list_date":"fldKNFSK9VDuAOR4o","sold_date":"fldkJCcFiZspqayZ5"}

def post(table_id, records):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_id}"
    data = json.dumps({"records": records}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  ERROR {e.code}: {body[:200]}")
        return None

coins = [
    {"cm":{"coin_id":"COIN-002","country":"オーストラリア","year":"2024","denom":"1ドル","material":"銀99.9%","weight":"31.1g","diameter":"40mm","grader":"NGC","grade":"PF70 Ultra Cameo","tags":["PF70","First Releases"]},
     "ys":{"record_id":"YS-002","price":85000,"date":"2025-11-20","days":7,"note":"即決売り"},
     "sr":{"record_id":"SR-002","purchase":280.00,"currency":"USD","shipping":20.00,"tariff":2000,"total":44000.00,"site":"eBay"},
     "ls":{"record_id":"LS-002","title":"2024 オーストラリア ウェッジテイルイーグル 1oz銀貨 NGC PF70 UC FR","desc":"ジョン・マーカンティデザイン。NGC最高鑑定PF70。","start_price":70000,"buynow":89000,"sold_price":85000,"list_date":"2025-11-13","sold_date":"2025-11-20"}},
    {"cm":{"coin_id":"COIN-003","country":"カナダ","year":"2025","denom":"5ドル","material":"銀99.99%","weight":"31.1g","diameter":"38mm","grader":"PCGS","grade":"PF69 DCAM","tags":["PF69","Advance Releases"]},
     "ys":{"record_id":"YS-003","price":45000,"date":"2026-01-10","days":10,"note":"オークション落札"},
     "sr":{"record_id":"SR-003","purchase":180.00,"currency":"USD","shipping":15.00,"tariff":1500,"total":30750.00,"site":"eBay"},
     "ls":{"record_id":"LS-003","title":"2025 カナダ メイプルリーフ 1oz銀貨 PCGS PF69 DCAM","desc":"カナダ王立造幣局。PCGS準最高鑑定。","start_price":38000,"buynow":48000,"sold_price":45000,"list_date":"2025-12-31","sold_date":"2026-01-10"}},
    {"cm":{"coin_id":"COIN-004","country":"イギリス","year":"2024","denom":"2ポンド","material":"銀99.9%","weight":"31.21g","diameter":"38.61mm","grader":"NGC","grade":"PF70 Ultra Cameo","tags":["PF70","Shield Privy"]},
     "ys":{"record_id":"YS-004","price":72000,"date":"2025-10-05","days":12,"note":"Shield Privy人気"},
     "sr":{"record_id":"SR-004","purchase":320.00,"currency":"USD","shipping":22.00,"tariff":2800,"total":54000.00,"site":"eBay"},
     "ls":{"record_id":"LS-004","title":"2024 イギリス ブリタニア 1oz銀貨 NGC PF70 UC Shield Privy","desc":"限定Shield Privy版。NGC最高鑑定。","start_price":65000,"buynow":75000,"sold_price":72000,"list_date":"2025-09-23","sold_date":"2025-10-05"}},
    {"cm":{"coin_id":"COIN-005","country":"アメリカ","year":"2024","denom":"1ドル","material":"銀99.9%","weight":"31.1g","diameter":"40.6mm","grader":"NGC","grade":"PF70 Ultra Cameo","tags":["PF70","First Releases","サインラベル"]},
     "ys":{"record_id":"YS-005","price":120000,"date":"2026-01-20","days":5,"note":"サインラベル即完売"},
     "sr":{"record_id":"SR-005","purchase":350.00,"currency":"USD","shipping":25.00,"tariff":3000,"total":58500.00,"site":"eBay"},
     "ls":{"record_id":"LS-005","title":"2024 アメリカンイーグル 1oz銀貨 NGC PF70 UC FR サインラベル","desc":"エリザベス・ジョーンズサイン。超人気。","start_price":100000,"buynow":128000,"sold_price":120000,"list_date":"2026-01-15","sold_date":"2026-01-20"}},
    {"cm":{"coin_id":"COIN-006","country":"ニュージーランド","year":"2025","denom":"1ドル","material":"銀99.9%","weight":"31.1g","diameter":"40mm","grader":"PCGS","grade":"PF70 DCAM","tags":["PF70"]},
     "ys":{"record_id":"YS-006","price":55000,"date":"2026-02-01","days":18,"note":"やや回転遅い"},
     "sr":{"record_id":"SR-006","purchase":260.00,"currency":"USD","shipping":30.00,"tariff":2500,"total":45500.00,"site":"Heritage Auctions"},
     "ls":{"record_id":"LS-006","title":"2025 NZ キウイ 1oz銀貨 PCGS PF70 DCAM","desc":"ニュージーランド造幣局。キウイデザイン。","start_price":48000,"buynow":58000,"sold_price":55000,"list_date":"2026-01-14","sold_date":"2026-02-01"}},
    {"cm":{"coin_id":"COIN-007","country":"イギリス","year":"2023","denom":"5ポンド","material":"銀92.5%","weight":"28.28g","diameter":"38.61mm","grader":"NGC","grade":"PF69 Ultra Cameo","tags":["PF69"]},
     "ys":{"record_id":"YS-007","price":38000,"date":"2025-09-15","days":21,"note":"回転やや遅い"},
     "sr":{"record_id":"SR-007","purchase":190.00,"currency":"USD","shipping":18.00,"tariff":1600,"total":32600.00,"site":"eBay"},
     "ls":{"record_id":"LS-007","title":"2023 イギリス 戴冠式記念 5ポンド銀貨 NGC PF69 UC","desc":"チャールズ3世戴冠式記念。","start_price":33000,"buynow":40000,"sold_price":38000,"list_date":"2025-08-25","sold_date":"2025-09-15"}},
    {"cm":{"coin_id":"COIN-008","country":"オーストリア","year":"2025","denom":"1.5ユーロ","material":"銀99.9%","weight":"31.1g","diameter":"37mm","grader":"NGC","grade":"PF70 Ultra Cameo","tags":["PF70","限定発行"]},
     "ys":{"record_id":"YS-008","price":68000,"date":"2026-02-15","days":8,"note":"限定版即売れ"},
     "sr":{"record_id":"SR-008","purchase":280.00,"currency":"USD","shipping":20.00,"tariff":2400,"total":46400.00,"site":"MA-Shops"},
     "ls":{"record_id":"LS-008","title":"2025 オーストリア フィルハーモニー 1oz銀貨 NGC PF70 UC 限定版","desc":"ウィーンフィル限定プルーフ。NGC最高鑑定。","start_price":60000,"buynow":72000,"sold_price":68000,"list_date":"2026-02-07","sold_date":"2026-02-15"}},
    {"cm":{"coin_id":"COIN-009","country":"南アフリカ","year":"2024","denom":"1ランド","material":"銀92.5%","weight":"33.93g","diameter":"38.7mm","grader":"NGC","grade":"PF70 Ultra Cameo","tags":["PF70","First Releases"]},
     "ys":{"record_id":"YS-009","price":95000,"date":"2025-12-20","days":9,"note":"クルーガーランド人気"},
     "sr":{"record_id":"SR-009","purchase":420.00,"currency":"USD","shipping":25.00,"tariff":3500,"total":70000.00,"site":"eBay"},
     "ls":{"record_id":"LS-009","title":"2024 南アフリカ クルーガーランド 1oz銀貨 NGC PF70 UC FR","desc":"クルーガーランドプルーフ。NGC最高鑑定。","start_price":85000,"buynow":98000,"sold_price":95000,"list_date":"2025-12-11","sold_date":"2025-12-20"}},
    {"cm":{"coin_id":"COIN-010","country":"アメリカ","year":"2025","denom":"1ドル","material":"銀99.9%","weight":"31.1g","diameter":"40.6mm","grader":"PCGS","grade":"PF70 DCAM","tags":["PF70","Advance Releases","サインラベル","限定発行"]},
     "ys":{"record_id":"YS-010","price":180000,"date":"2026-03-01","days":3,"note":"超高額即完売"},
     "sr":{"record_id":"SR-010","purchase":550.00,"currency":"USD","shipping":30.00,"tariff":5000,"total":90000.00,"site":"eBay"},
     "ls":{"record_id":"LS-010","title":"2025 アメリカンイーグル 1oz銀貨 PCGS PF70 DCAM AR サインラベル 限定","desc":"2025年限定版。PCGS最高鑑定。ダブルサインラベル。超希少。","start_price":150000,"buynow":198000,"sold_price":180000,"list_date":"2026-02-26","sold_date":"2026-03-01"}},
    {"cm":{"coin_id":"COIN-011","country":"中国","year":"2024","denom":"10元","material":"銀99.9%","weight":"30g","diameter":"40mm","grader":"NGC","grade":"PF69 Ultra Cameo","tags":["PF69"]},
     "ys":{"record_id":"YS-011","price":25000,"date":"2025-08-10","days":30,"note":"回転遅い・薄利"},
     "sr":{"record_id":"SR-011","purchase":130.00,"currency":"USD","shipping":15.00,"tariff":1200,"total":22200.00,"site":"eBay"},
     "ls":{"record_id":"LS-011","title":"2024 中国 パンダ 30g銀貨 NGC PF69 UC","desc":"中国造幣局パンダシリーズ。NGC準最高鑑定。","start_price":22000,"buynow":28000,"sold_price":25000,"list_date":"2025-07-11","sold_date":"2025-08-10"}},
]

ok = 0
skip = 0
for c in coins:
    cid = c["cm"]["coin_id"]
    # coin_master - tagsで422エラーになる場合はタグなしでリトライ
    cm_rec = {CM_F[k]: v for k, v in c["cm"].items()}
    r = post(CM, [{"fields": cm_rec}])
    if r is None:
        # タグ除外でリトライ
        cm_rec2 = {CM_F[k]: v for k, v in c["cm"].items() if k != "tags"}
        r = post(CM, [{"fields": cm_rec2}])
    if r is None:
        print(f"  SKIP {cid}")
        skip += 1
        continue
    print(f"{cid} coin_master: {r['records'][0]['id']}")
    # yahoo_sales
    ys_rec = {YS_F["record_id"]: c["ys"]["record_id"], YS_F["coin_id"]: cid, YS_F["price"]: c["ys"]["price"], YS_F["date"]: c["ys"]["date"], YS_F["url"]: f"https://page.auctions.yahoo.co.jp/jp/auction/ex{cid[-3:]}", YS_F["days"]: c["ys"]["days"], YS_F["note"]: c["ys"]["note"]}
    r = post(YS, [{"fields": ys_rec}])
    if r: print(f"  yahoo: {r['records'][0]['id']}")
    # sourcing
    sr_rec = {SR_F["record_id"]: c["sr"]["record_id"], SR_F["coin_id"]: cid, SR_F["purchase"]: c["sr"]["purchase"], SR_F["currency"]: c["sr"]["currency"], SR_F["shipping"]: c["sr"]["shipping"], SR_F["tariff"]: c["sr"]["tariff"], SR_F["total"]: c["sr"]["total"], SR_F["site"]: c["sr"]["site"], SR_F["url"]: f"https://www.ebay.com/itm/ex{cid[-3:]}"}
    r = post(SR, [{"fields": sr_rec}])
    if r: print(f"  sourcing: {r['records'][0]['id']}")
    # listing
    ls_rec = {LS_F["record_id"]: c["ls"]["record_id"], LS_F["coin_id"]: cid, LS_F["title"]: c["ls"]["title"], LS_F["desc"]: c["ls"]["desc"], LS_F["start_price"]: c["ls"]["start_price"], LS_F["buynow"]: c["ls"]["buynow"], LS_F["sold_price"]: c["ls"]["sold_price"], LS_F["list_date"]: c["ls"]["list_date"], LS_F["sold_date"]: c["ls"]["sold_date"]}
    r = post(LS, [{"fields": ls_rec}])
    if r: print(f"  listing: {r['records'][0]['id']}")
    ok += 1

print(f"\n=== {ok}件成功 / {skip}件スキップ ===")
