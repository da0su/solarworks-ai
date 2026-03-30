# SolarWorks AI — 事業統合リポジトリ

> **GitHubが正本（Single Source of Truth）です。**
> ローカル・セッション・個人記憶には依存しません。

---

## 概要

楽天ROOM自動投稿BOT + コイン仕入れリサーチを運営する事業統合リポジトリ。

| 事業 | ディレクトリ | 概要 |
|------|------------|------|
| 楽天ROOM BOT | `rakuten-room/bot/` | Playwright自動投稿・90〜100件/日 |
| コイン仕入れリサーチ | `coin_business/` | eBay/ヤフオク/海外オークション分析 |
| Slack連携 | `slack_bridge.py` | キャップ⇔サイバー AI間通信ブリッジ |

---

## 3台体制

| 端末 | 役割 |
|------|------|
| **サイバーさん（Desktop B）** | 本番基盤・24時間稼働（正本） |
| **キャップさん（ノート型）** | 開発・移行の主担当 |
| **レッツさん（Desktop A）** | 既存本番維持 |

---

## 起動方法

### サイバーさん（本番）— 毎回の起動手順

```bat
cd C:\Users\<username>\solarworks-ai
git pull
startup_all.bat
```

起動されるウィンドウ：
- `CYBER-SCHEDULER` — 楽天ROOM自動投稿
- `CYBER-WATCH`     — Slack Bridge 監視
- `CYBER-GUARDIAN`  — watch 自動復旧（self-heal）

### キャップさん（開発）

```bat
startup_cap.bat
```

---

## 復旧入口

セッションが飛んだ・新環境の場合、この順で読む：

```
1. gpt_mousiokuri/gpt_bootstrap.txt        ← 起動プロンプト
2. gpt_mousiokuri/gpt_handoff_latest.md    ← 最新状態
3. gpt_mousiokuri/gpt_recovery_runbook.md  ← 復旧手順（coin BOT）
4. coin_business/KNOWLEDGE.md              ← 全実装参照
5. CLAUDE.md                               ← AIへの設計指示
```

---

## 主要コマンド

### 楽天ROOM BOT

```bash
cd rakuten-room/bot
python run.py auto          # 完全自動運用（一発）
python run.py health        # 異常検知
python run.py report --slack # 朝レポート+Slack
```

### コイン仕入れリサーチ

```bash
cd coin_business
python run.py count         # DB件数確認
python run.py stats --clean --time  # 相場レポート
python run.py update-yahoo  # ヤフオク差分更新
python run.py update-ebay   # eBay差分更新
python run.py overseas-watch --source heritage  # 海外オークション監視
```

### Slack Bridge

```bash
python slack_bridge.py watch           # 常時監視
python slack_bridge.py watch-guardian  # watch自動復旧（別ウィンドウ）
python slack_bridge.py state-summary   # 状態確認
python slack_bridge.py send-task --task daily-check --to cyber
```

---

## Git運用ルール

### 更新フロー（必須）

```bash
git status          # 変更確認
git add <files>     # 必要ファイルのみ
git commit -m "feat/fix/docs(scope): 内容"
git push
```

### commitメッセージ規則

```
feat(coin): add buy-limit calculation
fix(watch): guardian heartbeat detection
docs(runbook): update recovery steps
refactor(bridge): consolidate ACK handling
chore(deps): update requirements
```

### 禁止事項

- `git push` 前に運用継続（ローカルだけ最新はNG）
- `.env` / APIキー を commit
- DBバックアップ本体を push（手順のみGit管理）

---

## ディレクトリ構造

```
solarworks-ai/
├── rakuten-room/bot/      ← 楽天ROOM BOT
├── coin_business/         ← コイン仕入れリサーチ
├── gpt_mousiokuri/        ← AI申し送り・復旧ドキュメント（正本）
│   ├── gpt_bootstrap.txt
│   ├── gpt_handoff_latest.md
│   ├── gpt_recovery_runbook.md
│   ├── gpt_handoff_db_safety.md
│   └── gpt_handoff_premium_price1.md
├── ops/
│   ├── scheduler/         ← 楽天ROOMスケジューラー
│   ├── automation/        ← watch/guardian batループ
│   └── notifications/     ← Slack/VOICEVOX通知
├── slack_bridge.py        ← AI間通信ブリッジ
├── startup_all.bat        ← サイバー全サービス起動（先頭でgit pull）
├── startup_cap.bat        ← キャップ起動
├── startup_cyber.bat      ← サイバー軽量起動
├── CLAUDE.md              ← AI設計指示（このリポジトリの憲法）
└── state/                 ← システム状態（system_state.json）
```

---

## セキュリティ

- `.env` は `.gitignore` 済み — **絶対にcommitしない**
- 各サービスの `.env.example` に必要変数を明示
- DBバックアップ本体はローカルのみ

---

*更新日: 2026-03-30 / 管理: キャップさん（開発） / 本番: サイバーさん（Desktop B）*
