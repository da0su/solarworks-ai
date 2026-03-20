# コインリサーチ事業 - 設計書 v1.0

## 概要

仮想通貨マーケットの情報を収集・整理・分析し、CEOの意思決定を支援する情報基盤。
「実行」ではなく「判断支援」を目的とし、情報の精度・継続的蓄積・再現性ある分析を重視する。

## 6つの前提条件

| # | 前提 | 要点 |
|---|------|------|
| 1 | 判断支援 | 自動売買ではなく、分析結果をCEOに提示 |
| 2 | データ中心 | 全データをタイムスタンプ付きで蓄積、再分析可能 |
| 3 | レイヤー分離 | 取得と分析を完全分離、ロジック再利用可能 |
| 4 | アウトプット設計 | 結論→根拠→データの順で可視化 |
| 5 | 自動化・監視 | スケジューラで自動実行、異常値は即時通知 |
| 6 | 統制・独立性 | coin_business/ 配下で完結、他事業と非共有 |

## アーキテクチャ

```
┌──────────────────────────────────────────────────┐
│              coin_business                        │
│                                                   │
│  ┌─────────────────┐   ┌─────────────────┐       │
│  │   Collectors     │   │   Analyzers     │       │
│  │ (scripts/        │──▶│ (scripts/       │       │
│  │  collectors/)    │   │  analyzers/)    │       │
│  └────────┬────────┘   └────────┬────────┘       │
│           │                      │                │
│           ▼                      ▼                │
│  ┌──────────────┐      ┌──────────────┐          │
│  │   data/      │      │  outputs/    │          │
│  │ (蓄積層)     │      │ (レポート層)  │          │
│  └──────────────┘      └──────────────┘          │
│                                │                  │
│                                ▼                  │
│                       ┌──────────────┐           │
│                       │ Slack通知     │           │
│                       └──────────────┘           │
└──────────────────────────────────────────────────┘
```

## データパイプライン

### Phase 1: Collectors（データ取得）

| データ種別 | 取得元 | 保存先 |
|-----------|--------|--------|
| 価格データ | CoinGecko / Binance API | data/prices/ |
| 出来高 | CoinGecko / Binance API | data/volume/ |
| ニュース | CryptoPanic / RSS | data/news/ |
| SNSトレンド | Twitter API / Reddit API | data/sns/ |
| オンチェーン | Etherscan / Blockchain.com | data/onchain/ |

### Phase 2: Analyzers（分析処理）

- 価格トレンド分析（移動平均、RSI、MACD等）
- 出来高異常検知
- ニュースセンチメント分析
- SNSトレンド相関分析
- オンチェーン指標分析（アクティブアドレス、ハッシュレート等）

### Phase 3: Outputs（レポート生成）

形式: 「結論→根拠→データ」

```
outputs/
├── daily/          # 日次レポート
│   └── YYYY-MM-DD_daily.json
└── weekly/         # 週次レポート
    └── YYYY-WXX_weekly.json
```

## フォルダ構成

```
coin_business/
├── DESIGN.md                    # 本設計書
├── config.py                    # 設定（APIキー、パス、閾値）
├── run.py                       # CLIエントリーポイント
│
├── scripts/
│   ├── collectors/              # 取得スクリプト（Phase 1）
│   │   ├── __init__.py
│   │   ├── price_collector.py   # 価格データ取得
│   │   ├── volume_collector.py  # 出来高データ取得
│   │   ├── news_collector.py    # ニュース取得
│   │   ├── sns_collector.py     # SNSトレンド取得
│   │   └── onchain_collector.py # オンチェーンデータ取得
│   │
│   └── analyzers/               # 分析スクリプト（Phase 2）
│       ├── __init__.py
│       ├── trend_analyzer.py    # 価格トレンド分析
│       ├── anomaly_detector.py  # 異常値検知
│       ├── sentiment_analyzer.py # ニュースセンチメント
│       └── report_generator.py  # レポート生成
│
├── data/                        # 蓄積層（タイムスタンプ付き）
│   ├── prices/                  # 価格データ
│   ├── volume/                  # 出来高データ
│   ├── news/                    # ニュースデータ
│   ├── sns/                     # SNSデータ
│   └── onchain/                 # オンチェーンデータ
│
├── outputs/                     # レポート出力層
│   ├── daily/                   # 日次レポート
│   └── weekly/                  # 週次レポート
│
├── logs/                        # 実行ログ
└── docs/                        # ドキュメント
```

## 監視・異常検知ルール

| 検知項目 | 条件 | アクション |
|---------|------|-----------|
| 急騰 | 1h で +5% 以上 | Slack即時通知 |
| 急落 | 1h で -5% 以上 | Slack即時通知 |
| 出来高異常 | 24h平均の3倍超 | Slack即時通知 |
| 重大ニュース | センチメントスコア極端値 | Slack即時通知 |

## Scheduler（自動実行計画）

```python
SCHEDULE = [
    {"time": "06:00", "action": "collect_all",    "desc": "全データ取得"},
    {"time": "06:30", "action": "analyze_all",    "desc": "全分析実行"},
    {"time": "07:00", "action": "daily_report",   "desc": "日次レポート生成＋Slack通知"},
    {"time": "12:00", "action": "collect_prices",  "desc": "価格データ中間取得"},
    {"time": "18:00", "action": "collect_all",    "desc": "全データ取得（夕方）"},
    {"time": "18:30", "action": "analyze_all",    "desc": "全分析実行（夕方）"},
    {"time": "19:00", "action": "evening_report", "desc": "夕方サマリー＋Slack通知"},
]
```

## MVP（最小実装）

### MVP Phase 1: 価格データ取得
1. CoinGecko API で主要通貨の価格・出来高を取得
2. タイムスタンプ付きJSON保存
3. 基本的なログ出力

### MVP Phase 2: トレンド分析
1. 移動平均・変化率の計算
2. 急騰・急落の検知
3. 分析結果をJSON出力

### MVP Phase 3: レポート＋通知
1. 日次レポート生成（結論→根拠→データ）
2. Slack通知連携
3. 週次サマリー自動生成

## 必要なAPI Key / 環境変数

```
COINGECKO_API_KEY=       # CoinGecko API（無料枠あり）
BINANCE_API_KEY=         # Binance API（オプション）
BINANCE_API_SECRET=      # Binance API Secret
CRYPTOPANIC_API_KEY=     # CryptoPanic ニュースAPI
ETHERSCAN_API_KEY=       # Etherscan API
SLACK_BOT_TOKEN=         # Slack通知用（既存共用可）
SLACK_APP_TOKEN=         # Slack通知用（既存共用可）
```

## 重要ルール

1. **他事業と完全独立**: coin_business/ 内で完結。bots/coin_bot/（物理コイン転売）とは別事業
2. **取得と分析は必ず分離**: collectors/ と analyzers/ を混在させない
3. **データは常にタイムスタンプ付き**: ファイル名に日時を含め、再分析可能に
4. **アウトプットは結論ファースト**: レポートは「結論→根拠→データ」の順
5. **CEOの判断を支援**: BOTは情報提示のみ、投資判断はCEO
6. **環境統制ルール厳守**: 絶対パス使用、旧環境アクセス禁止
