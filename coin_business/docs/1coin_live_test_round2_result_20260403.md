# 1コイン実戦テスト Round 2 結果報告
**実施日**: 2026-04-03
**ルート**: HIGH_GRADE / YEAR_DELTA
**実施者**: Cap (COO)
**判定**: **NO BUY**

---

## 提出物A: 1コイン仕入れメモ

### 探索対象 (staging分析より選定)

| シリーズ | staging件数 | Yahoo参照価格 | eBay買い上限 |
|---------|------------|-------------|-------------|
| France 20F Napoleon | 34件 | MS65=¥298,000 / MS62=¥249,800 | MS65=$1,572 / MS62=$1,318 |
| Japan 1 Yen Meiji 旧1円銀貨 | 4件 | MS64=¥224,000 / MS62=¥75,000 | MS64=$1,182 / MS62=$396 |
| GB Sovereign Gold | 61件 | MS65推定¥120,000 | MS65=$633 |
| US $20 Double Eagle | 13件 | MS62=¥801,000 | MS62=$4,226 |

### Longlist集計
**結果: 0件** (BUY候補なし)

---

## 提出物B: 証拠束

### B-1. France 20F Napoleon

**eBay検索クエリ**: `20 francs france napoleon gold graded`
**発見件数**: 2件 (US発送)

| eBay価格 | グレード | 発送元 | 総費用(JPY) | Yahoo参照 | 利益 | ROI | 判定 |
|---------|---------|-------|-----------|---------|-----|-----|-----|
| $1,550 | NGC (未明記) | US | ¥254,470 | ¥249,800 (MS62) | -¥29,650 | -11.7% | ❌ |
| $1,995 | NGC MS62 | US | ¥326,738 | ¥249,800 (MS62) | -¥101,918 | -31.2% | ❌ |

**NG理由**: eBay最安値$1,550でもMS62参照¥249,800に対して赤字。
MS65基準¥298,000で試算しても$1,550→利益¥-4,670(ROI-1.8%)で不成立。

---

### B-2. Japan 1 Yen Meiji 旧1円銀貨

**eBay検索クエリ**: `Meiji Japan 1 yen silver coin graded`
**発見件数**: 5件

| eBay価格 | 特記 | 発送元 | 問題点 | 判定 |
|---------|-----|-------|-------|-----|
| $400 | CHOPMARKED NGC HIGH GRADE | US | **チョップマーク付き** ≠ staging参照 | ❌ |
| $400 | CHOPMARKED NGC HIGH GRADE | US | **チョップマーク付き** ≠ staging参照 | ❌ |
| $500 | CHOPMARKED NGC HIGH GRADE | US | **チョップマーク付き** ≠ staging参照 | ❌ |
| $413 | NGC AU Grade Cleaned | JP | JP発送(US/UK規則違反) + Cleaned(Details) | ❌ |
| $1,100 | GIN stamp NGC | US | GIN印(押印あり)、ROI=11.1%(<15%) | ❌ |

**NG理由**:
- $400-500のチョップマーク付きコインはYahoo参照(ノーマーク品)と別物
- チョップマーク付きの実売価はMS64 ¥224,000より大幅に低い（¥30,000-50,000レンジ）
- $1,100 GIN印付き: 総費用¥181,390 vs Yahoo MS64 ¥224,000×0.9=¥201,600 → 利益¥20,210 ROI 11.1% → **15%基準未達**

---

### B-3. GB Sovereign Gold

**eBay検索クエリ**: `gold sovereign NGC PCGS graded coin`
**発見件数**: 1件

| eBay価格 | 内容 | 発送元 | 問題点 |
|---------|-----|-------|-------|
| $10 | NGC/PCGS Graded Coin (タイトルのみ) | GB | 実物コインの出品ではない疑い |

**NG理由**: 有効な出品なし。MS63-65クラスのUK発送Sovereignは在庫不足。

---

### B-4. US $20 Double Eagle (再確認)

**eBay検索クエリ**: `double eagle $20 gold NGC MS62`
**発見件数**: 8件

| eBay価格 | グレード | 発送元 | 総費用(JPY) | Yahoo参照 | 利益 | ROI |
|---------|---------|-------|-----------|---------|-----|-----|
| $4,738 | NGC MS62 (Saint-Gaudens) | US | ¥772,273 | ¥801,000 | -¥51,373 | -6.7% |
| $4,738 | NGC MS62 (Liberty Head) | US | ¥772,273 | ¥801,000 | -¥51,373 | -6.7% |
| $4,754 | NGC MS62 1908 No Motto | US | ¥774,788 | ¥801,000 | -¥53,888 | -7.0% |
| $4,924 | NGC MS62 (Liberty) | US | ¥802,469 | ¥801,000 | -¥81,569 | -10.2% |

**NG理由**: eBay最安$4,738 vs 買い上限$4,226 → 全件赤字。
eBayとYahoo間の価格差は7-10%程度で裁定余地なし。

---

## 提出物C: BUY/NO BUY 最終判定

### **判定: NO BUY**

### ゲート通過状況サマリー

| ゲート | 基準 | Round1 (CERT_EXACT) | Round2 (HIGH_GRADE/YEAR_DELTA) |
|------|------|-------------------|-------------------------------|
| Gate 1 | conf≥0.7, cert_company, 単品 | 4件通過 | 347件プール |
| Gate 2 | CERT_EXACT/HIGH_GRADE/YEAR_DELTA | 0件(slab不一致) | シリーズ別分析実施 |
| Gate 3 | Details/問題グレード除外 | — | JP旧1円銀貨 Cleanedを除外 |
| Gate 4 | profit>0, ROI≥15% | 0件 | **0件** |
| Gate 5 | US/UK発送, 単品, 画像整合 | — | — |

### 根本的な問題

1. **グレード不一致リスク**: eBayの安値商品はチョップマーク付き・印押しなど別バリエーション
2. **市場価格均衡**: US Double Eagleはグローバル金相場に連動しeBay≒Yahoo
3. **France 20F**: 流動性高いが価格帯($1,550+)がYahoo参照を上回る
4. **GB Sovereign**: 対象グレード帯がUS在庫薄

### 次ステップ提案 (CAO判断待ち)

| 優先度 | アクション | 期待効果 |
|-------|-----------|---------|
| P1 | France 20F のeBay Completed Sold検索(落札相場把握) | 実際の成約価格でYahoo比較精度向上 |
| P2 | Heritage Auction での日本・欧州コイン落札記録調査 | staging高値帯の妥当性検証 |
| P3 | stagingのチョップマーク有無フラグ付与 | 旧1円銀貨の正確な相場分離 |

---

## 計算式確認

```
総費用 = eBay価格(USD) × 145 × 1.12 + 2,750
純利益 = Yahoo落札価格(JPY) × 0.90 - 総費用
ROI = 純利益 / 総費用
買い上限 = Yahoo参照価格 × 0.90 × 0.85 / 145
```

---

*Round 1 + Round 2 合計: NO BUY*
*参照スクリプト: scripts/_r2_ebay_focused.py, scripts/_r2_ebay_debug.py*
