# KAPIBARAN サイト緊急修正 v3.1 完了レポート (Codex review 反映版)

- **実施日時**: 2026-05-18 14:24 JST
- **公開 URL**: https://www.kapibaran.com/
- **CMS / テーマ**: WordPress 6.9.4 + SWELL (kapibaran-child)
- **モード**: ZERO-TOUCH AUTONOMOUS EXECUTION (CEO 完全自律承認)
- **総実行時間**: 187.6 秒 (7 ステップ orchestrator)
- **基盤**: v3 (commit 8c860ed) に Codex review (22 回目, REJECT 10 issues) を反映
- **バックアップ**: PRIMARY のみ実体保存. LEGACY は pointer ファイルのみ.
  - PRIMARY: `kapibaran-site/state/v3_backups/<ts>/` ← 実体 (manifest + 全 file)
  - LEGACY: `state/v3_backups/<ts>/_pointer.json` ← primary_path 参照のみ (互換)
  - 含まれるファイル: pages.json / posts.json / media_index.json / settings.json /
    customizer_css.txt / categories.json / tags.json / menus.json / manifest.json
  - 各 file sha256 + bytes は manifest.json に集約

---

## 1. Codex review (REJECT 10 issues) への対応

### 1.1 致命的 (HIGH)

| # | issue | 対応 |
|---|---|---|
| #1 | CSS display:none で隠蔽 (§6 隠蔽禁止抵触) | `content/custom_css_v3.py` から `.kbv2-pd__benefits {display:none}` を削除、`content/pages_v2.py` も markup ごと削除、`deploy_v3_compliance.py` に `MARKUP_STRIP_PATTERNS` を追加して `<ul class="kbv2-pd__benefits">…</ul>` を regex で物理 strip (3 件削除済) |
| #2 | セーフティネット置換が draft 3 件のみ | `deploy_v3_compliance.py` に `EXPECTED_PUBLISH_PAGE_IDS = {25,26,27,28,74,75,124,125}` を hardcode + 公開 page ID リストを明示 log 出力 + 欠落時 ABORT |
| #3 | 検証範囲不整合 (7 件 vs 14/15 件) | `verify_v3.py` の URL リストを `EXPECTED_URL_COUNT=15` で hardcode (TOP/about/products/footcare/treadmill/contact/tokushoho/terms/privacy/journal-cat + journal個別5) — 件数不一致時 FATAL |
| #4 | EC URL `href="#"` 残置 = 誤誘導 | `pages_v3.py` の EC ボタンを `<span class="kbv3-cta--disabled" aria-disabled="true" role="link" tabindex="-1">…（準備中）</span>` に変更 — クリック不可 + ARIA 明示 + CSS で grayscale + pointer-events:none. verify で `href="#"` count = **0** 確認済 |
| #5 | バックアップ不在 | `automation/backup_v3_snapshot.py` を新規実装 — REST API で全 page/post/media/settings/customizer CSS を JSON snapshot + SHA256 ハッシュ取得。失敗時 ABORT (exit 2)。`run_v3_1_full.py` で Step 0 として gating |

### 1.2 重要 (MEDIUM)

| # | issue | 対応 |
|---|---|---|
| #6 | 検証ロジック受動的 | `verify_v3.py` に陽性 assertion 群を追加: `classification_badge` (kbv3-pd__classification), `msrp_note` (※実際の販売価格), `support_note` (kbv3-pd__support-note), `ec_disabled_marker` (data-todo="ec-url-pending"), `canonical_present`, `canonical_slug_based` (privacy), `http_200`, `no_redirect`, `not_noindex`, `canonical_match`, `no_hidden_markup` |
| #7 | ログ監査性不足 | `logs/verify_v3_result.json` の各 URL に: HTTP status / final URL / canonical / noindex / image_breakdown (img_tag + bg-url 内訳 + src sample) / chunk_assertions / all_assertions / body_sha256 / body_bytes を記録 |
| #8 | canonical 不整合 (`?page_id=3`) | `pages_v3.py` の `build_privacy()` を `deploy_v3_pages.py` の TOP_LEVEL_PAGES に追加し `/privacy/` slug-based URL でデプロイ。旧 `page_id=3` (slug=privacy-policy) は draft 化。verify で canonical が `/privacy/` で終わることを `canonical_slug_based` assertion で陽性確認 |

### 1.3 軽 (LOW)

| # | issue | 対応 |
|---|---|---|
| #9 | ガバナンス: runbook 化 | `kapibaran-site/RUNBOOK_v3.md` 新規作成 — ドライラン (`--dry-run`) / 本番デプロイ / 検証 / ロールバック / 緊急停止 / 通知設定 / 既知制約 |
| #10 | 通知抑制 default | `run_v3_1_full.py` default silent (capture mode) — `--verbose` で従来出力。失敗時のみ `KAPIBARAN_V3_SLACK_WEBHOOK` or `SLACK_WEBHOOK_URL` 経由で `<!channel>` 集約通知。webhook 未設定なら依存 0 |

---

## 2. 検証結果 (verify_v3.py — 強化版)

**OVERALL: PASS  (15/15)** — `logs/verify_v3_result.json`

| # | URL | HTTP | canonical | image | 禁止表現 | hidden markup | chunk assertions |
|---|---|---|---|---|---|---|---|
| 1 | https://www.kapibaran.com/ | 200 | 一致 | 25 | 0 | 0 | canonical OK |
| 2 | https://www.kapibaran.com/about/ | 200 | 一致 | 3 | 0 | 0 | canonical OK |
| 3 | https://www.kapibaran.com/products/ | 200 | 一致 | 6 | 0 | 0 | canonical OK |
| 4 | https://www.kapibaran.com/products/footcare-kb-fc01/ | 200 | 一致 | 5 | 0 | 0 | classification + msrp_note + support_note + canonical + ec_disabled_marker 全 PASS |
| 5 | https://www.kapibaran.com/products/treadmill-kb-tm01/ | 200 | 一致 | 6 | 0 | 0 | 同上 5 chunk 全 PASS |
| 6 | https://www.kapibaran.com/contact/ | 200 | 一致 | 2 | 0 | 0 | canonical OK |
| 7 | https://www.kapibaran.com/tokushoho/ | 200 | 一致 | 2 | 0 | 0 | canonical OK |
| 8 | https://www.kapibaran.com/terms/ | 200 | 一致 | 2 | 0 | 0 | canonical OK |
| 9 | https://www.kapibaran.com/privacy/ | 200 | 一致 (`/privacy/`) | 2 | 0 | 0 | canonical + canonical_slug_based 両 PASS |
| 10 | https://www.kapibaran.com/category/journal/ | 200 | 一致 | 12 | 0 | 0 | canonical OK |
| 11 | https://www.kapibaran.com/foot-self-care-five-min/ | 200 | 一致 | 12 | 0 | 0 | canonical OK |
| 12 | https://www.kapibaran.com/home-fitness-routine/ | 200 | 一致 | 12 | 0 | 0 | canonical OK |
| 13 | https://www.kapibaran.com/premium-daily-life/ | 200 | 一致 | 12 | 0 | 0 | canonical OK |
| 14 | https://www.kapibaran.com/material-selection/ | 200 | 一致 | 12 | 0 | 0 | canonical OK |
| 15 | https://www.kapibaran.com/adult-fitness-habits/ | 200 | 一致 | 12 | 0 | 0 | canonical OK |

**GLOBAL_FORBIDDEN 14 種 全 0 件** (送料無料 / ★★★★★ / お客様の声 / 血流を促 / 血行を促 / 1年メーカー保証 / 国内サポート対応 / エアバッグ式マッサージ / むくみ解消 / 脂肪燃焼 等)

**hidden markup 0 件** (`<ul class="kbv2-pd__benefits">…</ul>`, `<section class="kap-reviews">…</section>` 等が DOM に残存していないことを陽性確認)

---

## 3. compliance 結果 (`logs/compliance_v3_result.json`)

- pages_checked: 15 件 (status=any)
- 公開 page IDs scanned: `[25, 26, 27, 28, 29, 73, 74, 75, 124, 125, 178]` — EXPECTED 公開 page (25/26/27/28/74/75/124/125) は全件 scan 済
- pages_updated: 3 件 (旧 v1 draft 商品: footcare-device / smart-treadmill / shapewear-set)
  - 各 1 件 markup strip = `<ul class="kbv2-pd__benefits">…</ul>` を物理削除
- phrase_replacements_total: 0 件 (v3 上書きで既に置換済)
- markup_strips_total: 3 件
- missing_expected_publish_pages: なし

公開ページ (id=25/26/27/28/74/75/124/125) は deploy_v3_pages.py で v3 content に上書き済のため compliance 走査で再置換は発生せず (= 既に法令準拠状態)。draft の旧 v1 商品 3 件のみ benefits markup が残っていたので物理 strip 実施。

---

## 4. バックアップ snapshot

PRIMARY 実体: `kapibaran-site/state/v3_backups/<YYYYMMDD_HHMMSS>/`
LEGACY 互換: `state/v3_backups/<YYYYMMDD_HHMMSS>/_pointer.json` (primary_path 参照のみ)

| file | 概要 |
|------|---|
| pages.json | 全 page (status=any, context=edit raw 含む) |
| posts.json | 全 post (context=edit raw 含む) |
| media_index.json | 全 media の id/slug/url/title/mime_type |
| settings.json | WP site settings |
| customizer_css.txt | Customizer Custom CSS (取得不可時は空 or themes 応答) |
| categories.json | 全カテゴリー (description 含む) |
| tags.json | 全タグ (description 含む) |
| menus.json | `{available, items, note}` 形式 (REST 未対応時 note 明示) |
| manifest.json | 各 file の sha256 + bytes + counts + status + primary_path |

manifest.json で復旧時の整合性が保証される (sha256 不一致なら破損検知).
詳細: `RUNBOOK_v3.md` §4 ロールバック手順 / §7 taxonomy 復旧手順

---

## 5. v3.1 で追加 / 改変したファイル

```
kapibaran-site/
├── RUNBOOK_v3.md                       ← Codex #9 新規 runbook
├── COMPLETION_REPORT_v3_1.md           ← 本ファイル
├── automation/
│   ├── backup_v3_snapshot.py           ← Codex #5 新規 (REST 全件 snapshot + sha256)
│   ├── run_v3_1_full.py                ← Codex #5 #10 新規 orchestrator (backup gate + silent default)
│   ├── deploy_v3_compliance.py         ← Codex #1 #2 改修 (markup_strip + EXPECTED_PUBLISH_PAGE_IDS 検証)
│   ├── deploy_v3_pages.py              ← Codex #8 改修 (build_privacy 追加 + 旧 page_id=3 draft 化)
│   └── verify_v3.py                    ← Codex #3 #6 #7 #8 大改修 (URL hardcode + 陽性 assertion + audit log)
└── content/
    ├── custom_css_v3.py                ← Codex #1 改修 (display:none 削除)
    ├── pages_v2.py                     ← Codex #1 改修 (kbv2-pd__benefits markup 削除)
    └── pages_v3.py                     ← Codex #4 改修 (EC ボタン disabled span 化)
```

---

## 6. 残課題 (CEO 手動対応のみ)

### 6.1 EC モール URL 未設定 (`data-todo="ec-url-pending"` で disabled span 化済)
- Amazon / 楽天市場 / Yahoo! ショッピング × KB-FC01 / KB-TM01 = 計 6 URL
- 取得後 `content/products_v3.py` の `ec_urls` を更新し `automation/run_v3_1_full.py --verbose` で再デプロイ

### 6.2 サーバ / 外部サービス設定 (本タスク権限外)
- 特商法ページの「運営責任者」「所在地」 (現在「準備中」)
- info@kapibaran.com の MX レコード / メールサーバ実設定
- Contact Form 7 admin email
- OGP 画像 (attach_id=145) を SEO Simple Pack に設定
- Google Analytics 4 測定 ID
- reCAPTCHA キー

---

## 7. 実行コマンド (再現可能)

```bash
cd kapibaran-site

# ドライラン (バックアップのみ)
python automation/run_v3_1_full.py --dry-run --verbose

# 本番デプロイ (silent default)
python automation/run_v3_1_full.py

# 進捗詳細表示
python automation/run_v3_1_full.py --verbose

# 検証のみ
python automation/verify_v3.py
```

exit code: 0=ALL PASS / 1=verify fail / 2=backup fail (ABORT)

---

🤖 Generated by Claude Code (Opus 4.7) — Zero-Touch Autonomous Execution + Codex re-review loop
📅 Completed: 2026-05-18 14:24 JST
🎯 Mode: EMERGENCY COMPLIANCE FIX + Codex 10 REJECT issues 全反映
