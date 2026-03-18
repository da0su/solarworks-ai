# Solar Works 全体マップ

最終更新: 2026-03-18

## 事業フォルダ構成

```
solarworks-ai/
├── coin/                    # コインリサーチ事業
│   ├── research/            # リサーチ・分析
│   ├── trading/             # 取引関連
│   └── data/                # データ保管
│
├── rakuten-room/            # 楽天ROOM事業
│   ├── bot/                 # 投稿BOT (room_bot_v2)
│   ├── analytics/           # 分析
│   └── data/                # データ保管
│
├── china-oem/               # 中国OEM物販事業
│   ├── amazon/              # Amazon販路
│   ├── rakuten/             # 楽天販路
│   ├── other-ec/            # その他ECモール
│   └── shared/              # OEM共通（仕入先・原価等）
│
├── export-ec/               # 越境EC事業
│   ├── platform/            # プラットフォーム別
│   └── data/                # データ保管
│
├── web-media/               # Webメディア事業
│   ├── sites/               # サイト本体
│   ├── seo/                 # SEO施策
│   └── automation/          # 自動投稿・アフィリエイト・アドセンス
│
├── apps/                    # アプリ事業
│   ├── love-app/            # 恋愛相談アプリ
│   └── fortune-app/         # 占いアプリ
│
├── video-lab/               # 動画制作事業
│   ├── instagram/
│   │   ├── accounts/        # アカウント別管理
│   │   ├── production/      # 制作フロー
│   │   └── analytics/       # 分析
│   ├── tiktok/
│   │   ├── accounts/
│   │   ├── production/
│   │   └── analytics/
│   └── shared/              # 動画共通素材・テンプレ
│
├── ops/                     # 全社運用基盤
│   ├── notifications/       # 通知システム
│   ├── automation/          # 全社自動化ツール
│   ├── monitoring/          # 監視・ログ
│   └── credentials/         # 認証情報管理（.gitignore）
│
├── shared/                  # 全事業共通ライブラリ
│   ├── slack/               # Slack連携モジュール
│   ├── airtable/            # Airtable連携
│   ├── browser/             # ブラウザ制御
│   └── utils/               # 汎用ユーティリティ
│
└── docs/                    # ドキュメント
    ├── architecture/        # 設計書（このファイル）
    ├── runbooks/            # 運用手順書
    ├── onboarding/          # 引き継ぎ資料
    └── decisions/           # 意思決定ログ
```

## 既存コードの対応表

| 旧パス | 新パス | 状態 |
|--------|--------|------|
| `08_AUTOMATION/room_bot_v2/` | `rakuten-room/bot/` | 移行対象（本番コード） |
| `coin_business/` | `coin/` | 移行対象 |
| `notifications/` | `ops/notifications/` | ✅ 移行完了 (2026-03-18) |
| `_SHARED/` | `shared/` | 移行対象 |
| `bots/room_bot/` | - | 旧版（archive候補） |
| `bots/coin_bot/` | - | 旧版（archive候補） |
| `bots/slack/` | `shared/slack/` | 移行検討 |
| `00_OPERATIONS` 〜 `09_INTELLIGENCE` | - | 旧構造（archive候補） |
| `scheduler.py`, `watchdog.py` | `ops/automation/` | 移行対象 |

## 占有ルール

| フォルダ | ルール |
|----------|--------|
| 事業フォルダ（coin, rakuten-room等） | **排他占有** — 片方が触っている間、もう片方は触らない |
| ops/ | 原則契約A担当。契約Bは読み取りのみ。変更時は事前宣言 |
| shared/ | 原則契約A担当。契約Bは読み取りのみ。変更時は事前宣言 |
| docs/ | 両方から書き込みOK |

## 3フォルダの役割

| フォルダ | 判断基準 | 例 |
|----------|----------|-----|
| ops/ | コードとして動く + 事業横断 | 通知、監視、認証管理 |
| shared/ | コードとしてimportされる | Slack SDK、Airtable API、ブラウザ制御 |
| docs/ | 人間が読む | 設計書、手順書、意思決定記録 |
