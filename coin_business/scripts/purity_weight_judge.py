"""coin_slab_data の purity / weight_g 自動入力スクリプト

Phase 1: 既知コインパターンでルールベース判定（8割カバー目標）
Phase 2: 残りを Claude API にバッチで問い合わせ
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(r"C:\Users\砂田　紘幸\solarworks-ai\coin_business")
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env", override=True)

from scripts.supabase_client import get_client

# ============================================================
# 既知コインパターン定義
# ============================================================

# Each pattern: (regex_for_slab_line1_or_combined, purity, weight_g, description)
# Patterns are tested in order; first match wins.

GOLD_PATTERNS = [
    # === Sovereign family ===
    (r'\b5\s*Sov\b|\b5SOV\b', .917, 39.94, '5 Sovereign'),
    (r'\b2\s*Sov\b|\b2SOV\b', .917, 15.98, '2 Sovereign'),
    (r'\b1/2\s*Sov\b|\b1/2SOV\b|\bHalf\s*Sov', .917, 3.99, 'Half Sovereign'),
    # 1 Sov must come after 5Sov, 2Sov, 1/2Sov
    (r'\b1\s*Sov\b|\b1SOV\b|\b1\s*Sovereign\b|Great Britain Sov\b|G\.BRITAIN\s+1\s*SOV\b|G\.BRITAIN\s+1\s*Sov\b', .917, 7.99, 'Sovereign'),
    # "Sov" standalone (e.g. "1958 Great Britain Sov") — but not 1/2, 2, 5
    (r'(?<!\d[\s/])Sov\b(?!.*(?:1/2|Half))', .917, 7.99, 'Sovereign (standalone)'),

    # === Francs ===
    (r'\b40\s*F(?:r(?:ancs?)?)?(?:\b|$)|\bG40F\b', .900, 12.90, '40 Francs'),
    (r'\b20\s*F(?:r(?:ancs?)?)?(?:\b|$)|\bG20F\b|\b20F\b', .900, 6.45, '20 Francs'),
    (r'\b10\s*F(?:r(?:ancs?)?)?(?:\b|$)|\bG10F\b|\b10F\b', .900, 3.23, '10 Francs'),

    # === Ducat (Austria) ===
    (r'\b4\s*D(?:ucat)?\b.*(?:AUSTRIA|RESTRIKE)|\bAUSTRIA\s+4D\b', .986, 13.96, '4 Ducat Austria'),
    (r'\b1\s*D(?:ucat)?\b.*(?:AUSTRIA|RESTRIKE)|\bAUSTRIA\s+1D\b|\bDucat\b.*AUSTRIA', .986, 3.49, 'Ducat Austria'),
    # Standalone Ducat without explicit country — assume Austria
    (r'\bAUSTRIA\s+4D\b', .986, 13.96, '4 Ducat Austria'),

    # === US Gold Eagles ===
    (r'\$50\b.*(?:Eagle|1\s*oz|AGE)|G\$50|1\s*oz.*\$50', .9167, 33.93, 'Gold Eagle 1oz $50'),
    (r'\$25\b.*(?:Eagle|1/2\s*oz|AGE)|G\$25', .9167, 16.97, 'Gold Eagle 1/2oz $25'),
    (r'\$10\b.*(?:Eagle|1/4\s*oz|AGE)|G\$10', .9167, 8.48, 'Gold Eagle 1/4oz $10'),
    (r'\$5\b.*(?:Eagle|1/10\s*oz|AGE)|G\$5(?!0)', .9167, 3.39, 'Gold Eagle 1/10oz $5'),
    # US $20 Liberty/St. Gaudens
    (r'\$20\b', .900, 33.44, 'US $20 Gold'),
    # US $10 Liberty/Indian
    (r'\$10\b', .900, 16.72, 'US $10 Gold'),
    # US $5 Half Eagle
    (r'\$5\b', .900, 8.36, 'US $5 Gold'),
    # US $2.5 Quarter Eagle
    (r'\$2\.5\b|\$2\s*1/2\b', .900, 4.18, 'US $2.5 Gold'),

    # === Canadian Maple Leaf Gold ===
    (r'(?:CANADA|Canada).*G\$50|Maple.*G\$50|G\$50.*Maple', .9999, 31.10, 'Maple Leaf Gold 1oz'),
    (r'(?:CANADA|Canada).*G\$20|Maple.*G\$20', .9999, 15.55, 'Maple Leaf Gold 1/2oz'),
    (r'(?:CANADA|Canada).*G\$10|Maple.*G\$10', .9999, 7.78, 'Maple Leaf Gold 1/4oz'),
    (r'(?:CANADA|Canada).*G\$5|Maple.*G\$5', .9999, 3.13, 'Maple Leaf Gold 1/10oz'),

    # === Krugerrand ===
    (r'Krugerrand|KRUGERRAND', .9167, 33.93, 'Krugerrand'),

    # === Britannia Gold ===
    (r'(?:BRITANNIA|Britannia).*G(?:100P|£100|£?\s*100)|G\.BRITAIN\s+G100P|G\.Britain\s+G£?\s*100', .9999, 31.10, 'Britannia Gold 1oz'),
    (r'G\.BRITAIN\s+G50P|G\.Britain\s+G£?\s*50', .9999, 15.55, 'Britannia Gold 1/2oz'),
    (r'G\.BRITAIN\s+G25P|G\.Britain\s+G£?\s*25|G\.Britain\s+G£25|G\.Britain\s+G.*£25', .9999, 7.78, 'Britannia Gold 1/4oz'),
    (r'G\.BRITAIN\s+G10P|G\.Britain\s+G£?\s*10', .9999, 3.13, 'Britannia Gold 1/10oz'),

    # === Germany Mark ===
    (r'\b20\s*M(?:ark)?\b|\b20M\b|GERMANY\s+20M', .900, 7.97, '20 Mark Germany'),
    (r'\b10\s*M(?:ark)?\b|\b10M\b|GERMANY\s+10M', .900, 3.98, '10 Mark Germany'),

    # === Hungary Korona ===
    (r'HUNGARY.*(?:C20K|20K|20\s*Korona)', .900, 6.78, 'Hungary 20 Korona'),
    (r'HUNGARY.*(?:C10K|10K|10\s*Korona)', .900, 3.39, 'Hungary 10 Korona'),

    # === Austria Corona ===
    (r'AUSTRIA.*(?:100C|100\s*Corona)', .900, 33.88, 'Austria 100 Corona'),
    (r'AUSTRIA.*(?:20C|20\s*Corona)', .900, 6.78, 'Austria 20 Corona'),
    (r'AUSTRIA.*(?:10C|10\s*Corona)', .900, 3.39, 'Austria 10 Corona'),

    # === Panda Gold ===
    (r'CHINA\s+G500Y|Panda.*500\s*Yuan|500Y.*Panda', .999, 31.10, 'Panda Gold 1oz'),
    (r'CHINA\s+G200Y|Panda.*200\s*Yuan|200Y.*Panda', .999, 15.55, 'Panda Gold 1/2oz'),
    (r'CHINA\s+G100Y.*(?:Panda|\d{4})|Panda.*100\s*Yuan', .999, 31.10, 'Panda Gold 1oz (100Y)'),
    (r'CHINA\s+G50Y|Panda.*50\s*Yuan', .999, 15.55, 'Panda Gold 1/2oz (50Y)'),
    (r'CHINA\s+G10Y', .999, 3.11, 'Panda Gold 1/10oz'),

    # === Guinea ===
    (r'\bGuinea\b|\bGUINEA\b.*(?:G\.BRITAIN|Britain)', .917, 8.35, 'Guinea'),

    # === Zecchino (Venice) ===
    (r'\b1Z\b.*VENICE|\bZecchino\b', .997, 3.50, 'Zecchino Venice'),

    # === Colombia 5 Pesos ===
    (r'COLOMBIA.*G5P|Colombia.*5\s*Pesos.*Gold', .917, 7.99, 'Colombia 5 Pesos Gold'),

    # === Chile 100 Pesos ===
    (r'CHILE.*G100P|Chile.*100\s*Pesos.*Gold', .900, 20.34, 'Chile 100 Pesos Gold'),

    # === Romania 20 Lei ===
    (r'ROMANIA.*G20L|Romania.*20\s*Lei.*Gold', .900, 6.55, 'Romania 20 Lei Gold'),

    # === Swiss 20 Francs (already covered by 20F) ===
    # === Belgium 20 Francs (already covered by 20F) ===

    # === Italy Lire ===
    (r'ITALY.*40L\b|Italy.*40\s*Lire', .900, 12.90, 'Italy 40 Lire Gold'),
    (r'ITALY.*20L\b|Italy.*20\s*Lire', .900, 6.45, 'Italy 20 Lire Gold'),

    # === Netherlands ===
    (r'NETHERLANDS.*10G\b|Netherlands.*10\s*Gulden', .900, 6.73, 'Netherlands 10 Gulden'),

    # === Peru Sol ===
    (r'PERU.*G100S|Peru.*100\s*Soles', .900, 46.81, 'Peru 100 Soles Gold'),
    (r'PERU.*G50S|Peru.*50\s*Soles', .900, 23.41, 'Peru 50 Soles Gold'),
    (r'PERU.*G20S|Peru.*20\s*Soles', .900, 9.36, 'Peru 20 Soles Gold'),
    (r'PERU.*G10S|Peru.*10\s*Soles', .900, 4.68, 'Peru 10 Soles Gold'),
    (r'PERU.*G5S|Peru.*5\s*Soles', .900, 2.34, 'Peru 5 Soles Gold'),
    (r'PERU.*1\s*Libra|Peru.*Libra', .917, 7.99, 'Peru 1 Libra Gold'),

    # === Japan Gold ===
    (r'JAPAN.*20Y|Japan.*20\s*Yen|M\d+.*20\s*Yen|Meiji\s+20\s*Yen|20Y.*MUTSUHITO|JAPAN\s+20Y', .900, 16.67, 'Japan 20 Yen Gold'),
    (r'JAPAN.*10Y|Japan.*10\s*Yen|M\d+.*10\s*Yen|Meiji\s+10\s*Yen', .900, 8.33, 'Japan 10 Yen Gold'),
    (r'JAPAN.*5Y|Japan.*5\s*Yen|M\d+.*5\s*Yen|Meiji\s+5\s*Yen', .900, 8.33, 'Japan 5 Yen Gold (old)'),
    (r'\b2\s*Bu\b.*Japan|\bJapan.*2\s*Bu\b|\b2\s*Bu\b', .570, 3.00, 'Japan 2 Bu Gold'),

    # === Queen's Beasts / Royal Mint special (1oz gold) ===
    (r"Queen's\s+Beasts.*G£?\s*100|Queen's\s+Beasts.*G.*£?\s*500|G\.Brit(?:ain|ish)?\s+G.*£?\s*100", .9999, 31.10, "Queen's Beasts 1oz Gold"),
    (r"Queen's\s+Beasts.*G£?\s*25|G\.Brit(?:ain|ish)?\s+G.*£?\s*25", .9999, 7.78, "Queen's Beasts 1/4oz Gold"),

    # === Mexico ===
    (r'MEXICO.*50\s*Pesos|Mexico.*50P.*Gold', .900, 41.67, 'Mexico 50 Pesos Gold'),
    (r'MEXICO.*G20P|Mexico.*20\s*Pesos.*Gold', .900, 16.67, 'Mexico 20 Pesos Gold'),

    # === Russia ===
    (r'RUSSIA.*15\s*Roub|Russia.*15R|15R.*RUSSIA', .900, 12.90, 'Russia 15 Roubles'),
    (r'RUSSIA.*10\s*Roub|Russia.*10R|10R.*RUSSIA', .900, 8.60, 'Russia 10 Roubles'),
    (r'RUSSIA.*7\.50\s*Roub|Russia.*7R50|7R50.*RUSSIA', .900, 6.45, 'Russia 7.5 Roubles'),
    (r'RUSSIA.*5\s*Roub|Russia.*5R|5R.*RUSSIA', .900, 6.45, 'Russia 5 Roubles'),

    # === South Africa Pond/Pound ===
    (r'S\.\s*AFRICA.*1\s*POND|SOUTH\s*AFRICA.*POND|S\.AFR.*POND', .917, 7.99, 'South Africa 1 Pond'),
]

SILVER_PATTERNS = [
    # === Japan Yen ===
    (r'(?:Japan|JAPAN|M\d+|Meiji).*1\s*Yen|1\s*Yen.*Japan|\b1\s*Yen\b.*JNDA\s*01-10', .900, 26.96, 'Japan 1 Yen Silver'),
    (r'(?:Japan|JAPAN|M\d+).*50\s*Sen|\b50\s*Sen\b.*Japan', .800, 13.48, 'Japan 50 Sen Silver'),
    (r'(?:Japan|JAPAN|M\d+).*20\s*Sen|\b20\s*Sen\b.*Japan', .800, 5.39, 'Japan 20 Sen Silver'),
    (r'(?:Japan|JAPAN|M\d+).*10\s*Sen|\b10\s*Sen\b.*Japan', .800, 2.70, 'Japan 10 Sen Silver'),
    # Japan modern commemorative
    (r'(?:Japan|JAPAN|S\d+).*1000\s*Y(?:en)?|\b1000\s*Y\b.*Japan|Olympics.*JNDA\s*03-1', .925, 20.00, 'Japan 1000 Yen Silver'),
    (r'(?:Japan|JAPAN|S\d+).*100\s*Y(?:en)?|\b100\s*Y\b.*Japan', .600, 4.80, 'Japan 100 Yen Silver'),

    # === Silver Eagle ===
    (r'Silver\s*Eagle|\$1\b.*(?:Eagle|ASE)|S\$1\b', .999, 31.10, 'Silver Eagle 1oz'),

    # === Britannia Silver ===
    (r'BRITANNIA.*S£?\s*2|G\.BRITAIN.*S£?\s*2|Britannia.*Silver.*2', .999, 31.10, 'Britannia Silver 1oz'),

    # === Morgan Dollar ===
    (r'Morgan|\$1\b.*(?:Lib|MORGAN)', .900, 26.73, 'Morgan Dollar'),
    # US Silver Dollar generic (Peace, etc.)
    (r'\$1\b.*(?:Peace|PEACE)', .900, 26.73, 'Peace Dollar'),

    # === Trade Dollar ===
    (r'Trade\s*\$|Trade\s*Dollar|T\$1', .900, 27.22, 'Trade Dollar'),

    # === China Dollar ===
    (r'China.*\$1|China.*Dollar|Republic.*Dollar|(?:CHINA|China).*\$', .890, 26.70, 'China Dollar'),

    # === Crown (GB pre-1920) ===
    (r'G\.BRITAIN.*Crown|Britain.*Crown|CROWN.*(?:BRITAIN|Britain)', .925, 28.28, 'Crown GB'),

    # === Florin / 2 Shillings ===
    (r'(?:G\.BRITAIN|Britain).*(?:Florin|2\s*Shill|2S)', .925, 11.31, 'Florin GB'),

    # === Canada Maple Silver ===
    (r'(?:CANADA|Canada).*S\$5|Maple.*S\$5|Canada.*Maple.*Silver', .9999, 31.10, 'Canada Maple Silver'),

    # === Mexico Libertad Silver ===
    (r'MEXICO.*Libertad|Mexico.*Libertad.*1\s*oz', .999, 31.10, 'Mexico Libertad 1oz Silver'),

    # === Mexico 8 Reales ===
    (r'(?:MEXICO|Mexico).*8\s*R(?:eal(?:es)?)?', .903, 27.07, 'Mexico 8 Reales'),

    # === France ===
    (r'FRANCE.*5F|France.*5\s*Francs?', .900, 25.00, 'France 5 Francs Silver'),
    (r'FRANCE.*2F|France.*2\s*Francs?', .835, 10.00, 'France 2 Francs Silver'),
    (r'FRANCE.*1F|France.*1\s*Franc?', .835, 5.00, 'France 1 Franc Silver'),

    # === Japan Trade Dollar ===
    (r'(?:Japan|JAPAN|M\d+).*Trade\s*\$|Trade\$.*Japan', .900, 27.22, 'Japan Trade Dollar'),

    # === Generic GB silver ===
    (r'G\.BRITAIN.*(?:Half\s*Crown|1/2C)', .925, 14.14, 'GB Half Crown'),
    (r'G\.BRITAIN.*(?:Shilling|1S)', .925, 5.66, 'GB Shilling'),
    (r'G\.BRITAIN.*(?:6\s*Pence|6P|6D)', .925, 2.83, 'GB Sixpence'),

    # === Italy Papal States ===
    (r'ITALY.*(?:SCU|Scudo).*PAPAL|PAPAL.*SCU', .900, 26.70, 'Italy Papal States Scudi'),

    # === Ancient silver with weight in slab ===
    # Will be handled dynamically below
]

PLATINUM_PATTERNS = [
    # Platinum Eagle
    (r'Pt.*\$100|Platinum.*Eagle.*\$100|Pt£?\s*100.*Eagle', .9995, 31.10, 'Platinum Eagle 1oz'),
    (r'Pt.*\$50|Platinum.*Eagle.*\$50', .9995, 15.55, 'Platinum Eagle 1/2oz'),
    (r'Pt.*\$25|Platinum.*Eagle.*\$25|Pt.*£?\s*25', .9995, 7.78, 'Platinum Eagle 1/4oz'),
    (r'Pt.*\$10|Platinum.*Eagle.*\$10', .9995, 3.11, 'Platinum Eagle 1/10oz'),
    # Platinum Britannia
    (r'G\.BRIT(?:AIN|ain).*Pt.*£?\s*100|Britannia.*Pt.*100|Pt.*Britannia.*100', .9995, 31.10, 'Platinum Britannia 1oz'),
    (r'G\.BRIT(?:AIN|ain).*Pt.*£?\s*25|Britannia.*Pt.*25', .9995, 7.78, 'Platinum Britannia 1/4oz'),
    # Platinum Maple
    (r'(?:CANADA|Canada).*Pt.*\$50|Maple.*Pt.*50|Pt.*Maple.*50', .9995, 31.10, 'Platinum Maple 1oz'),
    (r'(?:CANADA|Canada).*Pt.*\$25|Maple.*Pt.*25', .9995, 7.78, 'Platinum Maple 1/4oz'),
    # Generic platinum 1oz
    (r'Pt.*1\s*oz|1\s*oz.*Pt', .9995, 31.10, 'Platinum 1oz generic'),
]


def extract_weight_from_text(text: str) -> float | None:
    """Try to extract weight from slab text like 'AR Stater (8.57g)' """
    m = re.search(r'\((\d+\.?\d*)\s*g\)', text)
    if m:
        return float(m.group(1))
    return None


def match_pattern(row: dict) -> tuple[float, float, str] | None:
    """Try to match a row against known patterns.
    Returns (purity, weight_g, description) or None.
    """
    line1 = row.get('slab_line1') or ''
    line2 = row.get('slab_line2') or ''
    line3 = row.get('slab_line3') or ''
    material = (row.get('material') or '').lower()
    combined = f"{line1} {line2} {line3}"

    # Pick pattern list based on material
    if material == 'gold':
        patterns = GOLD_PATTERNS
    elif material == 'silver':
        patterns = SILVER_PATTERNS
    elif material == 'platinum':
        patterns = PLATINUM_PATTERNS
    else:
        # Try all pattern lists
        for plist in [GOLD_PATTERNS, SILVER_PATTERNS, PLATINUM_PATTERNS]:
            for regex, purity, weight, desc in plist:
                if re.search(regex, combined, re.IGNORECASE):
                    return (purity, weight, desc)
        # Check for weight embedded in text
        w = extract_weight_from_text(combined)
        if w and material == 'unknown':
            return None  # Can't determine purity
        return None

    for regex, purity, weight, desc in patterns:
        if re.search(regex, combined, re.IGNORECASE):
            return (purity, weight, desc)

    # For ancient coins with weight in parentheses
    w = extract_weight_from_text(combined)
    if w:
        if material == 'silver':
            # Ancient silver - estimate purity based on era
            return (0.900, w, f'Ancient silver (weight from slab: {w}g)')
        elif material == 'gold':
            return (0.900, w, f'Ancient gold (weight from slab: {w}g)')

    return None


# ============================================================
# Phase 1: Rule-based matching
# ============================================================

def fetch_all_null_purity() -> list[dict]:
    """Fetch all completed_hit records with NULL purity."""
    client = get_client()
    all_data = []
    offset = 0
    while True:
        resp = (client.table('coin_slab_data')
                .select('id,slab_line1,slab_line2,slab_line3,material,grader,grade,coin_id')
                .eq('status', 'completed_hit')
                .is_('purity', 'null')
                .order('id')
                .range(offset, offset + 999)
                .execute())
        if not resp.data:
            break
        all_data.extend(resp.data)
        offset += 1000
        if len(resp.data) < 1000:
            break
    return all_data


def update_batch(updates: list[dict], batch_size: int = 50):
    """Update purity/weight_g in batches."""
    client = get_client()
    total = len(updates)
    for i in range(0, total, batch_size):
        batch = updates[i:i + batch_size]
        for rec in batch:
            client.table('coin_slab_data').update({
                'purity': rec['purity'],
                'weight_g': rec['weight_g'],
            }).eq('id', rec['id']).execute()
        done = min(i + batch_size, total)
        print(f"  Updated {done}/{total}")


# ============================================================
# Phase 2: Claude API for unknowns
# ============================================================

def ask_claude_batch(unknowns: list[dict], batch_size: int = 100) -> list[dict]:
    """Ask Claude API to estimate purity/weight for unknown coins."""
    import anthropic

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("WARNING: ANTHROPIC_API_KEY not set, skipping Claude API phase")
        return []

    client = anthropic.Anthropic(api_key=api_key)
    results = []

    for i in range(0, len(unknowns), batch_size):
        batch = unknowns[i:i + batch_size]
        # Build prompt
        lines = []
        for row in batch:
            line1 = row.get('slab_line1') or ''
            line2 = row.get('slab_line2') or ''
            line3 = row.get('slab_line3') or ''
            material = row.get('material') or ''
            grade = row.get('grade') or ''
            rid = row['id']
            lines.append(f"{rid}|{material}|{line1}|{line2}|{line3}|{grade}")

        coin_list = "\n".join(lines)
        prompt = f"""For each coin below, provide purity (decimal like .900) and weight in grams based on the coin identification.
If you cannot determine the values, use UNKNOWN for both.
Answer format: ID|purity|weight_g
One line per coin. No headers or explanation.

Coins (format: ID|material|slab_line1|slab_line2|slab_line3|grade):
{coin_list}"""

        print(f"  Asking Claude API: batch {i // batch_size + 1} ({len(batch)} coins)...")
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            answer = resp.content[0].text.strip()
            for line in answer.split('\n'):
                line = line.strip()
                if not line or '|' not in line:
                    continue
                parts = line.split('|')
                if len(parts) < 3:
                    continue
                rid = parts[0].strip()
                purity_str = parts[1].strip()
                weight_str = parts[2].strip()
                if purity_str == 'UNKNOWN' or weight_str == 'UNKNOWN':
                    continue
                try:
                    purity = float(purity_str)
                    weight = float(weight_str)
                    if 0 < purity <= 1.0 and 0 < weight < 1000:
                        results.append({'id': rid, 'purity': purity, 'weight_g': weight})
                except ValueError:
                    continue

            # Rate limit
            time.sleep(1)
        except Exception as e:
            print(f"  Claude API error: {e}")
            time.sleep(5)

    return results


# ============================================================
# Main
# ============================================================

def main():
    print("=" * 60)
    print("coin_slab_data purity/weight_g 自動入力")
    print("=" * 60)

    # Phase 1: Fetch all NULL purity records
    print("\n[Phase 1] Fetching records with NULL purity...")
    rows = fetch_all_null_purity()
    print(f"  Found {len(rows)} records with NULL purity")

    if not rows:
        print("  No records to process. Done.")
        return

    matched = []
    unmatched = []

    for row in rows:
        result = match_pattern(row)
        if result:
            purity, weight, desc = result
            matched.append({
                'id': row['id'],
                'purity': purity,
                'weight_g': weight,
                'desc': desc,
            })
        else:
            unmatched.append(row)

    print(f"\n  Pattern matched: {len(matched)} / {len(rows)} ({100 * len(matched) / len(rows):.1f}%)")
    print(f"  Unmatched: {len(unmatched)}")

    # Show match distribution
    desc_counts = {}
    for m in matched:
        desc_counts[m['desc']] = desc_counts.get(m['desc'], 0) + 1
    print("\n  --- Match distribution ---")
    for desc, cnt in sorted(desc_counts.items(), key=lambda x: -x[1]):
        print(f"    {desc}: {cnt}")

    # Update matched records
    if matched:
        print(f"\n[Phase 1] Updating {len(matched)} matched records...")
        update_batch(matched)
        print("  Done.")

    # Phase 2: Claude API for unknowns
    if unmatched:
        print(f"\n[Phase 2] {len(unmatched)} unmatched records")

        # Save unknown list for CEO
        unknown_file = PROJECT_ROOT / "data" / "purity_unknown_list.json"
        unknown_summary = []
        for row in unmatched:
            unknown_summary.append({
                'id': row['id'],
                'material': row.get('material', ''),
                'slab_line1': row.get('slab_line1', ''),
                'slab_line2': row.get('slab_line2', ''),
                'slab_line3': row.get('slab_line3', ''),
                'grade': row.get('grade', ''),
            })
        with open(unknown_file, 'w', encoding='utf-8') as f:
            json.dump(unknown_summary, f, ensure_ascii=False, indent=2)
        print(f"  CEO確認用リスト保存: {unknown_file}")

        # Ask Claude API
        print(f"\n[Phase 2] Claude APIで{len(unmatched)}件を推定...")
        claude_results = ask_claude_batch(unmatched)
        print(f"  Claude API resolved: {len(claude_results)} / {len(unmatched)}")

        if claude_results:
            print(f"\n[Phase 2] Updating {len(claude_results)} Claude-resolved records...")
            update_batch(claude_results)
            print("  Done.")

        still_unknown = len(unmatched) - len(claude_results)
        print(f"\n  最終UNKNOWN: {still_unknown}件（CEO手動確認が必要）")

    # Final summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Total processed:      {len(rows)}")
    print(f"  Phase1 rule-matched:  {len(matched)}")
    print(f"  Phase2 Claude API:    {len(unmatched)}")
    if unmatched:
        claude_ok = len([r for r in (locals().get('claude_results') or []) if r])
        print(f"  Phase2 resolved:      {claude_ok if 'claude_results' in dir() else 'N/A'}")
        still = len(unmatched) - (claude_ok if 'claude_results' in dir() else 0)
        print(f"  Final UNKNOWN:        {still}")
    print()


if __name__ == '__main__':
    main()
