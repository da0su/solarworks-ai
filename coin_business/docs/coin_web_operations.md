# coin_business/web 8502番ポート 運用ガイド
作成日: 2026-04-02
担当: COO (Cap)

---

## 運用構成

| 区分 | 担当 | 役割 |
|---|---|---|
| **本番（常設）** | **サイバーPC (Desktop B)** | 24時間常設。本線 |
| 予備（バックアップ） | Cap PC (ノート型) | 夜間停止あり。確認・緊急用のみ |

**CEOブックマーク導線**
→ `http://localhost:8502/index.html`
→ サイバーPC で開くことを前提とする

---

## 配信内容

| ファイル | URL |
|---|---|
| コイン仕入れDB UI | http://localhost:8502/index.html |
| コインデータ (JSON) | http://localhost:8502/data.json |

配信元ディレクトリ: `coin_business/web/`

---

## サイバーPC 起動方式

### 平常時（自動）
Windowsログイン後 30 秒で自動起動する。
→ タスクスケジューラー `SolarWorks-CoinWeb-8502` が `coin_web_loop.bat` を起動。

### 初回セットアップ手順
```
1. startup_all.bat を実行する（startup に含まれている / CYBER-COINWEB ウィンドウ確認）
2. 恒久化: ops\automation\setup_coinweb_autostart.bat を管理者として実行（1回のみ）
3. 再起動テスト: PC再起動後に http://localhost:8502/index.html にアクセスして確認
```

### 手動起動
```cmd
cd C:\Users\砂田　紘幸\solarworks-ai\ops\automation
coin_web_loop.bat
```

### タスク手動実行（テスト用）
```cmd
schtasks /run /tn "SolarWorks-CoinWeb-8502"
```

### タスク確認
```cmd
schtasks /query /tn "SolarWorks-CoinWeb-8502" /fo LIST
```

---

## Cap PC 起動方式（予備系）

`startup_cap.bat` 実行時に自動で 8502 を起動する（二重起動防止付き）。
Cap PC はバックアップ位置づけのため、本番常設運用には使用しない。

---

## 停電・再起動後の自動復旧

| 復旧方式 | 条件 |
|---|---|
| タスクスケジューラー（ONLOGON） | Windowsログイン後 30 秒で自動起動 |
| coin_web_loop.bat 内ループ | クラッシュ後 5 秒で自動再起動 |
| 停電 → 電源復帰後 PC 自動起動 | **BIOS 設定が必要**（下記参照） |

### 停電復帰時の PC 自動起動（要確認）
BIOS/UEFI の「AC Power Recovery」または「Restore on AC Power Loss」を
`Power On` に設定すると、停電後の電源復帰時にサイバーPC が自動起動する。
設定箇所は機種により異なる（BIOS 起動 → Power Management → AC Power Recovery）。
**この設定はサイバーPC の BIOS 画面で CEO/サイバーさんが確認してください。**

---

## 二重起動防止

`coin_web_loop.bat` および `startup_cap.bat` のどちらも、
起動前に `netstat -ano | find ":8502"` で既存プロセスを確認し、
起動済みの場合はスキップする。

---

## ログ

| ログファイル | 内容 |
|---|---|
| `logs/coin_web.log` | 8502 起動・停止・再起動ログ |

---

## トラブルシューティング

| 症状 | 確認先 | 対処 |
|---|---|---|
| `localhost:8502` が開かない | `netstat -ano \| find "8502"` | プロセスなし → `coin_web_loop.bat` を実行 |
| data.json が古い | `coin_business/web/data.json` の更新日時確認 | データ更新スクリプト実行 |
| タスクが動いていない | `schtasks /query /tn "SolarWorks-CoinWeb-8502"` | 管理者で `setup_coinweb_autostart.bat` を再実行 |

---

## 変更履歴

| 日付 | 変更内容 | 担当 |
|---|---|---|
| 2026-04-02 | `startup_cap.bat` に 8502 起動追加（予備系）| Cap |
| 2026-04-02 | `setup_coinweb_autostart.bat` 作成（サイバーPC 用）| Cap |
| 2026-04-02 | 本ドキュメント作成（本番=サイバーPC / 予備=Cap 整理）| Cap |
