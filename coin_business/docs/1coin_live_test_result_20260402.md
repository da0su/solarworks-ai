# 1コイン実戦テスト 結果メモ
作成日: 2026-04-02
担当: COO (Claude)
対象: Safe Mode 初回1コインテスト

---

## 最終判断

### **NO BUY**

> 良い案件が見つからなかったため、今回は仕入れを見送る。
> これは合格B（正しい見送り判断）に相当する。

---

## 1. 調査概要

coin_slab_data（Yahoo Japan落札実績ベース、2,927件）を起点に eBay Browse API で以下の候補タイプを検索・評価した。

| 対象コイン | Yahoo基準(ref2) | eBay最良価格 | 仕入れコスト | 判定 |
|---|---|---|---|---|
| France G100F (1857-1882) NGC/PCGS MS61-64 | ¥870K-¥1,193K | $5,696-$7,999 (~¥927K-¥1.3M) | ¥927K+ | **NG** eBay>>仕入限界 |
| GB Gold Sovereign 1965 NGC MS65 | ¥198K | $1,449-$1,500 (~¥238K) | ¥246K | **NG** eBay>>仕入限界 |
| Denmark 20 Krone 1873 NGC MS64 | ¥190K-¥244K | $1,499-$1,695 (~¥246K-¥278K) | ¥246K+ | **NG** eBay>>仕入限界 |
| Switzerland 20 Fr 1926-B PCGS MS66 | ¥195K | $1,638 (~¥268K) | ¥268K | **NG** eBay>>仕入限界 |
| Japan Trade Dollar M8(1875) PCGS MS62 | ¥803K | 同等クリーン品見つからず | — | **NG** Details品のみ |
| Japan Gold 1 Yen M7(1874) NGC MS64 | ¥801K | 同cert・同年No Hit | — | **NG** Listings見つからず |
| Japan Gold 1 Yen M4(1871) PCGS MS64 | ¥138K | $1,750 (発送元:東京) | ¥286K | **NG** ①Japan発送 ②M4相場<<費用 |
| Morgan Dollar (前回確認済) | ¥13K-25K | $190-$270 | ¥310K+ | **NG** 逆ざや確定 |
| British Sovereign NGC MS63-65 (各種) | ¥149K-¥499K | $1,450-$2,475 | ¥238K+ | **NG** eBay>>仕入限界 |

---

## 2. 最有力候補の詳細評価と除外理由

### 候補A: Japan Gold 1 Yen M4(1871) PCGS MS64（調査最終候補）

| 項目 | 内容 |
|---|---|
| eBayタイトル | 1871 M4 JAP TYPE 1 YEN GOLD PCGS MS64 💎 MEIJI BORDER HIGH DOT Y-9 |
| eBay URL | https://www.ebay.com/itm/317380075980 |
| eBay価格 | $1,750.00 (Buy It Now or Best Offer) |
| PCGS cert | 500979.64/45435819 |
| PCGS label | (1871) M4 Japan 1 Yen / JNDA 01-5 Au / High Dot Y-9 / MS64 |
| 発送元 | Tokyo, Japan |
| Seller | Coins From Japan (108件 95.7%) |
| Yahoo基準 | M7(1874) G1Y NGC MS64 cert 6652609-003 ref2=¥801,001 |
| M4(1871) Yahoo相場 | ¥115K-¥138K (coin_slab_data 実績) |

**除外理由（2点）:**

**① 発送元ルール違反**
eBay仕入れは「発送元US/UKのみ」が運用ルール（MEMORY.md: project_ebay_sourcing_rules）。
本件は東京発送 → 二重関税リスクあり → **除外**

**② Level A 価値不整合**
- Year差は3年（1871 vs 1874）で ±5年以内ではあるが、
- M4(1871) の Yahoo Japan 相場は ¥138K（NGC MS64基準）、
- eBay仕入れコスト = $1,750 × 145 × 1.12 + ¥2,750 = **¥286,950**
- → Yahoo Japan売却予想 ¥138K - 手数料10% ¥13.8K = **¥124,200 の手取り**
- → 利益 = ¥124,200 - ¥286,950 = **▲¥162,750（赤字）**

M7(1874) が ¥801K、M4(1871) が ¥138K という価格差は、単なる年号差ではなく **希少バリエーションの違い**によるもの。年号差±5年ルールの適用範囲外（価値根拠が一致しない）。

---

## 3. 構造的考察（次回への示唆）

### なぜ今回は候補が出なかったか

**A. Yahoo母集団ゼロ問題**
新パイプライン（yahoo_sold_lots）のデータがまだゼロ件（staging投入待ち）。
本来の仕組みでは Yahoo Japan 落札データ → seed → eBay マッチという流れだが、
今回は代替として coin_slab_data（旧来Yahoo実績）を使用した。

**B. 希少金貨はグローバル市場で同価格帯**
coin_slab_data 上位コインは France Piefort / Gothic Crown / Japan Meiji Gold 等の
**国際的に希少・高評価なコイン**が大半。eBayでも同等価格で流通 → 裁定余地なし。

**C. eBay API精度限界**
Browse API の keyword検索は cert-exact matching ができない。
同一 cert 番号の eBay 出品を検出するには Merchandising API か
cert直接検索 (NGC/PCGS公式) との連携が必要（将来改善余地）。

### 次回有望セクターの仮説

| 仮説 | 根拠 |
|---|---|
| 日本国内希少・海外認知低いコイン | 日本人コレクター需要 > 国際評価 → Japan premium |
| 近代欧州金貨 (フランス 20フラン ナポレオン型) | 量産品 → eBay豊富で安価、日本では美品需要あり |
| 米国コイン中の特定年/マーク | Yahoo Japan で特定バリエーション premium |

---

## 4. 提出物B：証拠束

| 項目 | 内容 |
|---|---|
| 調査スクリプト | scripts/_find_arb.py / _find_arb2.py / _find_arb3.py / _find_arb4.py / _find_arb5.py |
| 検索対象 coin_slab_data | buy_limit > 30K JPY & cert非NULL (上位60件精査) |
| eBay API確認件数 | 約200件の検索結果を評価 |
| 直接eBayページ確認 | M4 Japan G1Y PCGS MS64 (item 317380075980) |

---

## 5. 最終判断

```
最終判断: NO BUY

理由:
  1. 全候補においてeBayコスト >= Yahoo Japan売却見込み額（裁定余地なし）
  2. 唯一コスト面で可能性のあったM4(1871) Japan G1Y は
     ① 発送元Japan（US/UK only ルール違反）
     ② M4のYahoo Japan相場 ¥138K < 仕入れコスト ¥286K（赤字確定）
  3. Yahoo母集団データ未投入のため正式フローでの検索ができない状態

次のアクション:
  - Yahoo stagingデータ投入 → CEO承認 → main昇格 を優先
  - 正式seeder稼働後に再実施
  - 近代欧州量産型金貨セクターの追加調査（France 20 Franc Rooster型等）
```

---

> 「良い案件がなければ NO BUY で構いません。」— cap_1coin_live_test_guide.md §1
> 「無理買いしないことも合格。」— cap_1coin_live_test_guide.md §6
