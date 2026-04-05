"""4新カラム データ投入スクリプト

対象テーブル : coin_slab_data
対象カラム   :
  ref1_buy_limit_15pct_jpy  ← 基準1・粗利15%条件（= 現 ref1_buy_limit_jpy と同値）
  ref1_buy_limit_20k_jpy    ← 基準1・最低利益2万円条件
  ref2_buy_limit_15pct_jpy  ← 基準2・粗利15%条件
  ref2_buy_limit_20k_jpy    ← 基準2・最低利益2万円条件

前提条件     :
  - 上記4カラムがSupabase SQL EditorでDDL追加済みであること
  - バックアップ取得済みであること

実行方法     :
  python scripts/update_4columns.py            # 全件更新
  python scripts/update_4columns.py --dry-run  # ドライラン（DB書き込みなし）
  python scripts/update_4columns.py --limit 10 # 先頭N件のみ

計算式       :
  基準1
    net1 = int((premium_value_jpy + metal_value_jpy) * 0.9)
    ref1_15pct = int((int(net1 * 0.85) - 2750) / 1.1) if int(net1*0.85) > 2750 else 0
    ref1_20k   = int((net1 - 20000 - 2750) / 1.1) if net1 > 22750 else 0

  基準2
    net2 = int(ref2_yahoo_price_jpy * 0.9)
    ref2_15pct = int((int(net2 * 0.85) - 2750) / 1.1) if int(net2*0.85) > 2750 else 0
    ref2_20k   = int((net2 - 20000 - 2750) / 1.1) if net2 > 22750 else 0

禁止事項     :
  - ref1_buy_limit_jpy（既存カラム）の書き換えは一切しない
  - 上記4カラム以外には触れない
"""

import sys
import io
import time
import argparse
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / '.env')
from scripts.supabase_client import get_client


# ──────────────────────────────────────────────
# 計算ロジック（変更禁止）
# ──────────────────────────────────────────────

def calc_4columns(row: dict) -> dict | None:
    """4新カラムの値を計算して返す。計算不可なら None。"""
    prem  = row.get('premium_value_jpy')
    metal = row.get('metal_value_jpy')
    yahoo = row.get('ref2_yahoo_price_jpy')

    # 基準1: premium + metal が必須
    ref1_15 = None
    ref1_20 = None
    if prem is not None and metal is not None:
        net1 = int((prem + metal) * 0.9)
        cl1 = int(net1 * 0.85)
        ref1_15 = int((cl1 - 2750) / 1.1) if cl1 > 2750 else 0
        ref1_20 = int((net1 - 20000 - 2750) / 1.1) if net1 > 22750 else 0

    # 基準2: yahoo価格が必須
    ref2_15 = None
    ref2_20 = None
    if yahoo is not None and yahoo > 0:
        net2 = int(yahoo * 0.9)
        cl2 = int(net2 * 0.85)
        ref2_15 = int((cl2 - 2750) / 1.1) if cl2 > 2750 else 0
        ref2_20 = int((net2 - 20000 - 2750) / 1.1) if net2 > 22750 else 0

    # 基準1も基準2も計算不能な場合はスキップ
    if ref1_15 is None and ref2_15 is None:
        return None

    return {
        'ref1_buy_limit_15pct_jpy': ref1_15,
        'ref1_buy_limit_20k_jpy':   ref1_20,
        'ref2_buy_limit_15pct_jpy': ref2_15,
        'ref2_buy_limit_20k_jpy':   ref2_20,
    }


# ──────────────────────────────────────────────
# 検証: ref1_15pct ≒ 既存 ref1_buy_limit_jpy
# ──────────────────────────────────────────────

def verify_ref1_consistency(db, sample_n: int = 20) -> bool:
    """ref1_15pct の計算値が既存 ref1_buy_limit_jpy と一致することを確認"""
    resp = db.table('coin_slab_data').select(
        'management_no,premium_value_jpy,metal_value_jpy,ref1_buy_limit_jpy'
    ).eq('status', 'completed_hit').not_.is_('purity', 'null').limit(sample_n).execute()

    mismatches = []
    for r in resp.data:
        calc = calc_4columns(r)
        if calc is None:
            continue
        new_15 = calc['ref1_buy_limit_15pct_jpy']
        existing = r.get('ref1_buy_limit_jpy')
        if existing is not None and new_15 is not None:
            if abs(new_15 - existing) > 5:  # ±5円の丸め誤差は許容
                mismatches.append(f"  {r.get('management_no')}: 計算={new_15} 既存={existing} 差={new_15-existing}")

    if mismatches:
        print(f"  ⚠️  ref1_15pct 不一致: {len(mismatches)}件")
        for m in mismatches[:5]:
            print(m)
        return False
    else:
        print(f"  ✅ ref1_15pct 一致確認: {len(resp.data)}件 全OK（±5円以内）")
        return True


# ──────────────────────────────────────────────
# メイン
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='DB書き込みなし（計算のみ）')
    parser.add_argument('--limit', type=int, default=0, help='処理件数制限（0=全件）')
    args = parser.parse_args()

    mode = "DRY-RUN" if args.dry_run else "WRITE"
    print(f"=" * 60)
    print(f"4新カラム データ投入 [{mode}]")
    print(f"対象: coin_slab_data (status=completed_hit, purity NOT NULL)")
    print(f"=" * 60)

    db = get_client()

    # ── 事前確認: 4カラムの存在チェック ──
    print("\n[Step 1] DDLカラム存在確認")
    r = db.table('coin_slab_data').select('*').limit(1).execute()
    if not r.data:
        print("  ERROR: coin_slab_data が空です")
        sys.exit(1)
    cols = list(r.data[0].keys())
    required = ['ref1_buy_limit_20k_jpy', 'ref1_buy_limit_15pct_jpy',
                'ref2_buy_limit_20k_jpy', 'ref2_buy_limit_15pct_jpy']
    missing = [c for c in required if c not in cols]
    if missing:
        print(f"  ❌ カラム未存在: {missing}")
        print(f"  → Supabase SQL Editor で以下を実行してから再実行してください:")
        print(f"    ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref1_buy_limit_20k_jpy   integer;")
        print(f"    ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref1_buy_limit_15pct_jpy integer;")
        print(f"    ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref2_buy_limit_20k_jpy   integer;")
        print(f"    ALTER TABLE coin_slab_data ADD COLUMN IF NOT EXISTS ref2_buy_limit_15pct_jpy integer;")
        sys.exit(1)
    print(f"  ✅ 4カラム全て存在確認済み（{len(cols)}カラム）")

    # ── Step 2: ref1_15pct 整合性確認 ──
    print("\n[Step 2] ref1_15pct 計算整合性確認（20件サンプル）")
    if not verify_ref1_consistency(db, 20):
        print("  ❌ 整合性エラー。計算式を確認してください")
        sys.exit(1)

    # ── Step 3: 全件取得 ──
    print("\n[Step 3] 対象レコード取得")
    all_data = []
    last_id = '00000000-0000-0000-0000-000000000000'
    while True:
        resp = (db.table('coin_slab_data')
                .select('id,management_no,premium_value_jpy,metal_value_jpy,ref2_yahoo_price_jpy')
                .eq('status', 'completed_hit')
                .not_.is_('purity', 'null')
                .gt('id', last_id)
                .order('id')
                .limit(500)
                .execute())
        if not resp.data:
            break
        all_data.extend(resp.data)
        last_id = resp.data[-1]['id']
        if len(resp.data) < 500:
            break

    if args.limit > 0:
        all_data = all_data[:args.limit]
        print(f"  取得: {len(all_data)}件（--limit {args.limit}）")
    else:
        print(f"  取得: {len(all_data)}件（全件）")

    # ── Step 4: 計算・書き込み ──
    print(f"\n[Step 4] 計算{'（ドライラン）' if args.dry_run else '・書き込み'}")
    updated = 0; skipped = 0; errors = 0; anomalies = []
    start = time.time()

    for i, row in enumerate(all_data):
        mgmt = row.get('management_no', '-') or '-'
        calc = calc_4columns(row)
        if calc is None:
            skipped += 1
            continue

        # 異常チェック
        for col, val in calc.items():
            if val is not None and (val < -100000 or val > 50000000):
                anomalies.append(f"{mgmt} {col}={val}")

        if not args.dry_run:
            try:
                db.table('coin_slab_data').update(calc).eq('id', row['id']).execute()
                updated += 1
            except Exception as e:
                errors += 1
                anomalies.append(f"{mgmt} ERROR:{str(e)[:60]}")
        else:
            updated += 1  # dry-runでは常にカウント

        if (i + 1) % 500 == 0:
            elapsed = time.time() - start
            print(f"  進捗: {i+1}/{len(all_data)} | 更新={updated} skip={skipped} err={errors} | {elapsed:.0f}秒")

    elapsed = time.time() - start
    print(f"\n{'DRY-RUN' if args.dry_run else '書き込み'}完了")
    print(f"  更新: {updated}件 | スキップ: {skipped}件 | エラー: {errors}件")
    print(f"  所要: {elapsed:.1f}秒（{elapsed/60:.1f}分）")
    print(f"  異常: {len(anomalies)}件")
    if anomalies:
        for a in anomalies[:10]:
            print(f"    ⚠️  {a}")

    if args.dry_run:
        print("\n  → DRY-RUNのため書き込みなし。--dry-runフラグを外して再実行してください")
        return

    # ── Step 5: 書き込み後検証 ──
    if not args.dry_run:
        print("\n[Step 5] 書き込み後検証（サンプル5件）")
        resp = db.table('coin_slab_data').select(
            'management_no,ref1_buy_limit_jpy,ref1_buy_limit_15pct_jpy,'
            'ref1_buy_limit_20k_jpy,ref2_buy_limit_15pct_jpy,ref2_buy_limit_20k_jpy'
        ).eq('status', 'completed_hit').not_.is_('purity', 'null').limit(5).execute()

        print(f"  {'mgmt':7} | {'ref1(既存)':>10} {'ref1_15pct':>10} {'ref1_20k':>10} | {'ref2_15pct':>10} {'ref2_20k':>10}")
        print("  " + "-" * 75)
        for r in resp.data:
            mgmt = r.get('management_no', '-') or '-'
            print(f"  {mgmt:7} | {str(r.get('ref1_buy_limit_jpy') or ''):>10} "
                  f"{str(r.get('ref1_buy_limit_15pct_jpy') or ''):>10} "
                  f"{str(r.get('ref1_buy_limit_20k_jpy') or ''):>10} | "
                  f"{str(r.get('ref2_buy_limit_15pct_jpy') or ''):>10} "
                  f"{str(r.get('ref2_buy_limit_20k_jpy') or ''):>10}")

        # NULL率確認
        null_15 = db.table('coin_slab_data').select('id', count='exact').eq('status','completed_hit').not_.is_('purity','null').is_('ref1_buy_limit_15pct_jpy','null').limit(1).execute().count
        total_hit = db.table('coin_slab_data').select('id', count='exact').eq('status','completed_hit').not_.is_('purity','null').limit(1).execute().count
        print(f"\n  ref1_buy_limit_15pct_jpy NULL率: {null_15}/{total_hit} ({null_15/total_hit*100:.1f}%)")


if __name__ == '__main__':
    main()
