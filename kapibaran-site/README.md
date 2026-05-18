# KAPIBARAN サイト v2

CEO 確定 5 SKU + Coming Soon 構成での全面リビルド。
v1 (worktree `confident-murdock-256690`) の自動化基盤を流用しつつ、
v2 の判断ルールに従って実装している。

## 実商品 (5 SKU)

| SKU      | 商品名              | 価格    | カラー                        |
|----------|---------------------|---------|-------------------------------|
| KB-FC01  | フットケア家電       | ¥33,800 | ネイビー / ベージュ            |
| KB-TM01  | スマートトレッドミル | ¥49,800 | オレンジ / ホワイト / ブルー   |

## Coming Soon (2 カテゴリー)

- BODY CARE — ボディケア
- BODY SHAPING — ボディシェイピング

## ブランドカラー (CEO 5/13 確定)

- Deep Navy `#1F2A44`
- Sunset Amber `#C96E12`
- Off White `#F8F6F2`

## 構成

```
kapibaran-site/
├── automation/
│   ├── credentials.py          # Box の pass ファイルから WP 認証を読込
│   ├── wp_session.py           # Playwright persistent context
│   ├── wp_rest.py              # WP REST API クライアント
│   ├── deploy_v2_css.py        # Customizer に v2 CSS を投入
│   ├── deploy_v2_pages.py      # 全ページ upsert + 旧商品 draft 化
│   ├── deploy_v2_menus.py      # ヘッダー/フッターメニュー再構築
│   ├── verify_v2.py            # HTTP GET で反映検証
│   └── run_v2_full.py          # 上記 4 ステップを順に実行
├── content/
│   ├── products_v2.py          # 5 SKU マスタ
│   ├── pages_v2.py             # 各ページ HTML ビルダー
│   └── custom_css_v2.py        # サイト全体 CSS
├── state/                      # 実行時 state (pages_v2_deploy.json 等)
├── screenshots/                # Playwright スクリーンショット
├── logs/                       # ログ + verify 結果
└── COMPLETION_REPORT_v2.md     # CEO 提出レポート
```

## 実行

```bash
cd kapibaran-site
python automation/run_v2_full.py
```

## v1 との差分

- 商品を 3 → 2 種類 (5 SKU) に集約し、CEO 確定の価格に変更
- BODY CARE / BODY SHAPING を Coming Soon として明示
- カラーパレットを Beige から Navy + Amber + Off White に変更
  (CEO 5/13 確定のブランドカラーへ揃えるため)
- 旧 v1 商品詳細 (`footcare-device` / `smart-treadmill` / `shapewear-set`)
  は draft 化して公開停止
