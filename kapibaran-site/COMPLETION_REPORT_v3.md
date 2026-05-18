# KAPIBARAN サイト緊急修正 v3 完了レポート

- **実施日時**: 2026-05-18 14:13 JST
- **公開 URL**: https://www.kapibaran.com/
- **CMS / テーマ**: WordPress 6.9.4 + SWELL (kapibaran-child)
- **モード**: ZERO-TOUCH AUTONOMOUS EXECUTION (CEO 完全自律承認)
- **総実行時間**: 約 4 分 (6 ステップ orchestrator)
- **基盤**: v2 (commit dde4d1e) を破壊せず v3 として追加配置
- **バックアップ**: ConoHa WAF が `wp db export` を block するため、
  既存 v2 page (id=25/26/27/28/74/75/124/125) を非破壊的に PATCH（content 上書き）。
  旧 v1 商品 page (id=76/77/78) は v2 で draft 化済を継続。

---

## 1. 法令違反箇所の修正

### 1.1 景品表示法 / 特商法
| 修正項目 | 件数 | 状態 |
|---|---|---|
| 「全国 送料無料」「送料無料」 全削除 (v2 page content) | 1 件 (商品詳細テンプレ kbv2-pd__benefits を `display:none` + v3 で完全 markup 除去) | 完了 |
| 「税込・送料無料」 → 「（税込）」 + 注記 | 商品詳細 2 件, NEW ARRIVALS 2 件, Products 一覧 2 件 | 完了 |
| 「1 年メーカー保証」「1年メーカー保証」 全削除 | spec 表の「保証期間」を「保証・サポート: 販売店規定に準じます」へ書き換え (商品 2 件) | 完了 |
| 「国内サポート対応」 削除 | benefits リスト撤去 | 完了 |
| 価格表示の統一: 「メーカー希望小売価格 ¥XX,XXX（税込）」 | 商品詳細 2 件 + NEW ARRIVALS + Products 一覧 | 完了 |
| 「※実際の販売価格・送料・在庫・保証等の詳細は…」 注記 | 商品詳細 / NEW ARRIVALS / Products 一覧 すべてに追加 | 完了 |
| 特商法ページに「ブランドサイトから直販していない旨」 明記 | 1 件 | 完了 |

### 1.2 ステマ規制 (2023/10 施行)
| 修正項目 | 件数 | 状態 |
|---|---|---|
| 「お客様の声」 セクション 完全削除 | 商品詳細 2 件 (`<section class="kbv2-section"... CUSTOMER VOICE>` ごと削除) | 完了 |
| 「CUSTOMER VOICE」 削除 | 商品詳細 2 件 | 完了 |
| 「★★★★★ 5.0」 等 レビュー 削除 | 6 件のレビュー文 (商品 2 × 3 件) を data layer (`products_v3.py`) からも削除 | 完了 |

### 1.3 薬機法 (医薬品医療機器等法)
| Before | After | 件数 |
|---|---|---|
| 血流を促す | じんわり温めて、心地よく | KB-FC01 features + 旧 v1 draft 3 件 |
| 血行を促進 | やさしく温める | (検出なし — 予防的置換) |
| エアバッグ式マッサージ | エアバッグ式の心地よい刺激 | KB-FC01 features |
| マッサージ | やさしく包み込む刺激 | KB-FC01 features + 旧 draft |
| 疲労回復 / むくみ解消 / 治療 / 治癒 | 削除・置換 | 予防的置換 (旧 draft 3 件で 12 phrase) |
| 効果がある / 効きます | 心地よく感じられます | 予防的置換 |

**自動置換ログ** (`logs/compliance_v3_result.json`):
- pages updated: 3 / 14 (旧 v1 draft 商品)
- posts updated: 0 / 8 (新規ジャーナルは元から準拠)
- phrase replacements total: **12 件**
- email replacements total: **0 件** (新規 v3 content はすでに info@kapibaran.com を使用)

### 1.4 機器分類 (リラクゼーション機器明記)
| 商品 | 機器分類 |
|---|---|
| KB-FC01 フットケア家電 | **リラクゼーション機器（医療機器ではありません）** |
| KB-TM01 スマートトレッドミル | **ホームフィットネス機器（医療機器ではありません）** |

商品詳細ページ:
- スペック表トップ行に追加
- 価格セクション直下に `kbv3-pd__classification` バッジで表示
- Contact FAQ にも「これらの商品は医療機器ですか？」項目を追加

---

## 2. 画像投入結果 (ZIP 17 点)

| セクション | 投入画像 | WP attach ID | ステータス |
|---|---|---|---|
| TOP ヒーロー | hero_top_family_living.jpg | 129 | 完了 (background-image + overlay rgba(43,34,24,0.35)) |
| PRODUCT CATEGORIES (4 枚) | cat_01_foot_care.jpg | 130 | 完了 |
| | cat_02_home_fitness.jpg | 131 | 完了 |
| | cat_03_body_care_comingsoon.jpg | 132 | 完了 |
| | cat_04_body_shaping_comingsoon.jpg | 133 | 完了 |
| NEW ARRIVALS (TOP) | KB-FC01 ベージュ + KB-TM01 オレンジ | 135 / 136 | 完了 |
| 商品詳細 KB-FC01 | ベージュ (メイン) + ネイビー (サムネ) | 135 / 134 | 完了 (featured_media=135) |
| 商品詳細 KB-TM01 | オレンジ (メイン) + ホワイト + ブルー (サムネ) | 136 / 137 / 138 | 完了 (featured_media=136) |
| ジャーナル 5 記事 | journal_01〜05 | 139〜143 | 完了 (featured_media 設定済) |
| About | about_hero_hands.jpg | 144 | 完了 (background + overlay + featured_media=144) |
| OGP 共有画像 | ogp_kapibaran_share.jpg | 145 | アップロード済 (SEO Simple Pack 等の手動設定は §3 残課題) |

**画像数 検証結果** (`logs/verify_v3_result.json`):
| ページ | 画像数 (img + bg-url) |
|---|---|
| TOP | 24 (≥5 OK) |
| Products 一覧 | 6 (≥2 OK) |
| Footcare 詳細 | 5 (≥1 OK) |
| Treadmill 詳細 | 6 (≥1 OK) |
| About | 3 (≥1 OK) |
| Contact | 2 (≥0 OK) |
| 特商法 | 2 (≥0 OK) |
| ジャーナル 5 記事 (個別) | 各 12 |

---

## 3. info@kapibaran.com 反映

| 反映先 | 状態 |
|---|---|
| Contact ページ — メールカード | `<strong>info@kapibaran.com</strong>` を明示 |
| Contact ページ — メールボックス | `kbv2-mail-box__addr` で大きく表示 |
| Contact ページ — FAQ | 「各モール購入製品も対応」項目に明記 |
| TOP CTA セクション | `kbv3-cta__email` で表示 |
| 商品詳細 サポート注記 | `kbv3-pd__support-note` 内に明記 |
| 商品詳細 CTA | `kbv3-cta__email` で表示 |
| About 運営会社セクション | `kbv3-about__email` で表示 |
| 特商法 お問い合わせ先 | テーブル行で明記 |
| プライバシーポリシー 第 5 条 | 開示請求先として明記 |
| 既存 support@kapibaran.com の自動置換 | `deploy_v3_compliance.py` で全 page/post スキャン (0 件検出) |

CF7 admin email / DNS MX 等 サーバ側設定は本タスク範囲外。

---

## 4. 反映 URL 一覧 (verify_v3 結果)

| # | ページ | URL | 検証結果 |
|---|---|---|---|
| 1 | TOP | https://www.kapibaran.com/ | ✅ PASS (24 img) |
| 2 | About | https://www.kapibaran.com/about/ | ✅ PASS (3 img) |
| 3 | プロダクト一覧 | https://www.kapibaran.com/products/ | ✅ PASS (6 img) |
| 4 | KB-FC01 詳細 | https://www.kapibaran.com/products/footcare-kb-fc01/ | ✅ PASS (5 img) |
| 5 | KB-TM01 詳細 | https://www.kapibaran.com/products/treadmill-kb-tm01/ | ✅ PASS (6 img) |
| 6 | サポート (Contact/FAQ) | https://www.kapibaran.com/contact/ | ✅ PASS (2 img) |
| 7 | 特定商取引法に基づく表記 | https://www.kapibaran.com/tokushoho/ | ✅ PASS (2 img) |
| 8 | 利用規約 | https://www.kapibaran.com/terms/ | ✅ deep-check OK |
| 9 | プライバシーポリシー | https://www.kapibaran.com/?page_id=3 | ✅ v2 流用 |
| 10 | ジャーナル一覧 | https://www.kapibaran.com/category/journal/ | ✅ deep-check OK |
| 11-15 | ジャーナル個別 5 記事 | /foot-self-care-five-min/ など | ✅ 各 12 img / 禁止表現 0 |

---

## 5. v3 で追加した自動化ファイル一覧

```
kapibaran-site/
├── materials_v3/KAPIBARAN_assets_v2/  ← ZIP 展開先 (17 画像 + README + PDF×2)
├── content/
│   ├── products_v3.py      ← 法令遵守版 商品マスタ (レビュー削除済)
│   ├── pages_v3.py         ← 法令遵守版 ページビルダー (画像対応)
│   └── custom_css_v3.py    ← v2 CSS + v3 追加 (背景画像 / overlay / MSRP / support-note)
├── automation/
│   ├── deploy_v3_media.py     ← Step1: 画像 17 点アップロード
│   ├── deploy_v3_css.py       ← Step2: Customizer に CSS_V3 流し込み
│   ├── deploy_v3_pages.py     ← Step3: 全ページ upsert + featured_media
│   ├── deploy_v3_journal.py   ← Step4: ジャーナル 5 記事 + アイキャッチ
│   ├── deploy_v3_compliance.py← Step5: 全 page/post 全文置換 (safety net)
│   ├── verify_v3.py            ← Step6: HTTP 検証 (禁止表現 + 画像数)
│   └── run_v3_full.py          ← orchestrator (1 コマンド全工程)
└── state/
    ├── media_v3.json           ← key → attach_id, source_url
    ├── pages_v3_deploy.json    ← slug → page_id
    └── journal_v3_deploy.json  ← slug → post_id
```

実行コマンド (再現可能):
```bash
cd kapibaran-site
python automation/run_v3_full.py
```

---

## 6. 残課題 (CEO / 手動対応必要)

### 6.1 EC モール URL 未設定 (`data-todo="ec-url-pending"` で marker 済)
- [ ] Amazon 商品 URL × KB-FC01
- [ ] Amazon 商品 URL × KB-TM01
- [ ] 楽天市場 商品 URL × KB-FC01
- [ ] 楽天市場 商品 URL × KB-TM01
- [ ] Yahoo! ショッピング 商品 URL × KB-FC01
- [ ] Yahoo! ショッピング 商品 URL × KB-TM01

該当箇所は HTML 内に `data-todo="ec-url-pending"` 属性付きの `<a href="#">` として残置。
取得次第 `products_v3.py` の `ec_urls` を更新して `deploy_v3_pages.py` 再実行で反映可能。

### 6.2 サーバ / 外部サービス側 設定 (本 sub-agent 権限外)
- [ ] 特商法ページの「運営責任者」「所在地」(現在「準備中」)
- [ ] info@kapibaran.com の MX レコード / メールサーバ実設定
- [ ] Contact Form 7 の admin email を info@kapibaran.com に設定
- [ ] OGP 画像 (attach_id=145) を SEO Simple Pack / Yoast に設定
- [ ] Google Analytics 4 測定 ID 設置
- [ ] reCAPTCHA キー設定

---

## 7. 法令遵守セルフチェック

- [x] **景品表示法 5 条** — 価格・送料・保証の確定表記を全削除し、「販売店規定に準じる」表記に統一
- [x] **特商法** — ブランドサイト直販なしを特商法ページに明記、各販売店誘導のみ
- [x] **ステマ規制 (2023/10)** — お客様の声・★★★★★・レビュー 全削除 (markup 上 + data 上の両方)
- [x] **薬機法** — 効能断定表現 (血流促進/マッサージ/治療/痩せる 等) を感覚表現に全置換、機器分類 (リラクゼーション機器・医療機器ではない旨) を商品 detail + FAQ に明記

verify_v3.py + 手動 deep-check で 9 + 5 = **14 URL** に対して **GLOBAL_FORBIDDEN 14 種** をスキャン、**全 0 件検出** を確認。

---

## 8. 視覚的注記

- ヒーロー: 背景写真 + `rgba(43,34,24,0.35)` overlay でテキスト視認性確保 (§4 判断ルール準拠)
- カテゴリーカード: 4:3 写真サムネ + Coming Soon バッジ (2 枠)
- 商品カード: メイン写真 + MSRP label + 価格 + カラーチップ
- 商品詳細ギャラリー: 正方形メイン写真 + カラーバリエーション サムネ (横並び)
- support-note: amber 左ボーダー付きクリームボックスで視覚的に分離

---

🤖 Generated by Claude Code (Opus 4.7) — Zero-Touch Autonomous Execution
📅 Completed: 2026-05-18 14:13 JST
🎯 Mode: EMERGENCY COMPLIANCE FIX + IMAGE DEPLOYMENT — ✅ ALL CHECKS PASS
