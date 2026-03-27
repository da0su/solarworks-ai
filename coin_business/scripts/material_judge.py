"""Claude APIテキストベース素材判定スクリプト

coin_slab_data (status=completed_hit) の全件を Claude claude-sonnet-4-20250514 で
素材判定し、material / is_ancient カラムを更新する。
"""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")
load_dotenv(PROJECT_ROOT / ".env", override=True)

sys.path.insert(0, str(PROJECT_ROOT))
import anthropic
from scripts.supabase_client import get_client

BATCH_SIZE = 100
MODEL = "claude-sonnet-4-20250514"

ANCIENT_KEYWORDS = [
    "ROMAN EMPIRE", "ROMAN REPUBLIC", "MACEDON", "PTOLEMAIC",
    "SELEUKID", "SELEUCID", "PARTHIAN", "SASANIAN", "SASSANIAN",
    "BACTRIA", "BAKTRIA", "THRACE", "THRACIAN", "BYZANTINE",
    "GREEK", "ATTICA", "ATHENS", "SICILY", "CALABRIA",
    "LUCANIA", "BRUTTIUM", "CAMPANIA", "EGYPT ANCIENT",
    "JUDAEA", "PHOENICIA", "LYDIA", "MYSIA", "IONIA",
    "CARIA", "LYCIA", "PAMPHYLIA", "PISIDIA", "CILICIA",
    "CAPPADOCIA", "PONTOS", "BITHYNIA", "GALATIA",
    "ACHAEMENID", "KUSHAN", "INDO-GREEK", "CELTIC",
    "OSTROGOTHIC", "VISIGOTHIC", "MEROVINGIAN",
]

# NGC 5段階古代グレードシステム
ANCIENT_GRADES = ["FINE", "VF", "XF", "CH XF", "MS", "AU"]


def build_prompt(batch: list[dict]) -> str:
    """バッチ用プロンプトを生成"""
    lines = []
    for row in batch:
        text = " | ".join(filter(None, [
            row.get("grader", ""),
            row.get("slab_line1", ""),
            row.get("slab_line2", ""),
            row.get("slab_line3", ""),
            row.get("grade", ""),
        ]))
        lines.append(f'{row["id"]}: {text}')

    coin_list = "\n".join(lines)

    return f"""You are a numismatic expert. For each coin slab entry below, determine the PRIMARY material and whether it is an ancient coin.

MATERIAL RULES:
- "G" prefix in denomination (G100Y, G50D, G$5, etc.) = Gold
- "S" prefix in denomination (S$1, S10P, etc.) = Silver
- "Sovereign", "Guinea", "Ducat", "Florin (gold)" = Gold
- Dollar/Crown/Shilling/Yen/Mark/Franc without G/S prefix: check context
- "Pt" prefix = Platinum
- "Pd" or "Palladium" = Platinum (group as Platinum)
- Copper/Cent/Penny (pre-1982 US, pre-1860 UK) = Copper
- Modern small denominations (1 cent, 5 cents, 10 yen) = Base Metal
- Bi-metallic coins = Base Metal
- "Electrum" or "EL" denomination = Electrum
- Bronze mentioned explicitly = Bronze
- If unsure = UNKNOWN

ANCIENT COIN RULES:
- Keywords indicating ancient: ROMAN EMPIRE, ROMAN REPUBLIC, MACEDON, PTOLEMAIC, SELEUKID, PARTHIAN, SASANIAN, BYZANTINE, Greek city-states (ATTICA, SICILY, THRACE, etc.)
- Ancient coins typically use 5-point grading (Fine, VF, XF, CH XF, MS) without numeric suffixes like MS69
- Struck/hammered coinage before ~500 AD = ancient

OUTPUT FORMAT:
Return a JSON array. Each element: {{"id": "<uuid>", "material": "<material>", "is_ancient": true/false}}
Valid materials: Gold, Silver, Platinum, Copper, Base Metal, Electrum, Bronze, UNKNOWN

IMPORTANT: Return ONLY the JSON array, no other text.

COINS:
{coin_list}"""


def fetch_all_completed_hit() -> list[dict]:
    """status=completed_hit の全件を取得"""
    sb = get_client()
    all_data = []
    offset = 0
    cols = "id,grader,slab_line1,slab_line2,slab_line3,grade,material,is_ancient"
    while True:
        resp = (sb.table("coin_slab_data")
                .select(cols)
                .eq("status", "completed_hit")
                .range(offset, offset + 999)
                .execute())
        if not resp.data:
            break
        all_data.extend(resp.data)
        if len(resp.data) < 1000:
            break
        offset += 1000
    return all_data


def call_claude(prompt: str, client: anthropic.Anthropic) -> list[dict]:
    """Claude APIを呼び出して結果をパース"""
    resp = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    # JSON部分を抽出
    if text.startswith("["):
        return json.loads(text)
    # ```json ... ``` で囲まれている場合
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    raise ValueError(f"Could not parse response: {text[:200]}")


def update_records(results: list[dict]):
    """判定結果をSupabaseに反映"""
    sb = get_client()
    for r in results:
        update_data = {"material": r["material"]}
        if r.get("is_ancient") is not None:
            update_data["is_ancient"] = r["is_ancient"]
        sb.table("coin_slab_data").update(update_data).eq("id", r["id"]).execute()


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not found in .env")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("=== Material Judge: Claude Text-Based ===")
    print(f"Model: {MODEL}")
    print(f"Batch size: {BATCH_SIZE}")
    print()

    # 全件取得
    print("Fetching all completed_hit records...")
    rows = fetch_all_completed_hit()
    total = len(rows)
    print(f"Total records: {total}")
    print()

    # バッチ処理
    num_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    stats = {"Gold": 0, "Silver": 0, "Platinum": 0, "Copper": 0,
             "Base Metal": 0, "Electrum": 0, "Bronze": 0, "UNKNOWN": 0}
    ancient_count = 0
    errors = 0

    for i in range(0, total, BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch = rows[i:i + BATCH_SIZE]
        print(f"Batch {batch_num}/{num_batches} ({len(batch)} coins)...", end=" ", flush=True)

        try:
            prompt = build_prompt(batch)
            results = call_claude(prompt, client)

            # 結果を検証
            result_map = {r["id"]: r for r in results}
            valid_results = []
            for row in batch:
                if row["id"] in result_map:
                    r = result_map[row["id"]]
                    mat = r.get("material", "UNKNOWN")
                    if mat not in stats:
                        mat = "UNKNOWN"
                    stats[mat] += 1
                    if r.get("is_ancient"):
                        ancient_count += 1
                    valid_results.append(r)
                else:
                    print(f"\n  WARNING: No result for {row['id']}")
                    errors += 1

            # DB更新
            update_records(valid_results)
            print(f"OK ({len(valid_results)} updated)")

        except Exception as e:
            print(f"ERROR: {e}")
            errors += len(batch)

        # Rate limit対策
        if batch_num < num_batches:
            time.sleep(1)

    # サマリー
    print()
    print("=== SUMMARY ===")
    print(f"Total processed: {total}")
    print(f"Errors: {errors}")
    print(f"Ancient coins: {ancient_count}")
    print()
    print("Material distribution:")
    for mat, cnt in sorted(stats.items(), key=lambda x: -x[1]):
        if cnt > 0:
            print(f"  {mat}: {cnt} ({cnt/total*100:.1f}%)")


if __name__ == "__main__":
    main()
