# Phase C-7: pyautogui → Playwright 統一 設計書

**作成日**: 2026-05-05 | **見積工数**: 1-2週間 | **優先度**: 中

## 目的

VM 内 follow_rpa_vm.py の pyautogui ベース実装を Playwright ベースに統一し、
座標 hardcode (BASE_W=1920, BASE_H=909) からの脱却・解像度 drift の根本排除。

## 現状の問題

- 解像度依存: `_BTN_X = 1195`, `BASE_W = 1920` などハードコード
- OCR/色判定脆弱: `is_pink(r, g)` 閾値が照度・dark mode 等で誤判定
- 多重コードベース: HOST 側 follow_executor.py / follow_host_runner.py と
  VM 側 follow_rpa_vm.py が独立し、同じロジックの重複保守
- DOM 情報を取れない: rate_limit modal が出ても DOM-level で確認できない

## 既存資産

- `rakuten-room/bot/executor/follow_host_runner.py` (HOST 上 Playwright・既動作実績)
- `rakuten-room/bot/executor/follow_executor.py` (HOST 上 Playwright・古い実装)
- `rakuten-room/bot/executor/browser_manager.py` (action 別 profile 対応済 Phase A-2)

## 移行ステップ (4 段階)

### Step 1: HOST 上で Playwright follow を robust 化（3日）
- `follow_host_runner.py` をベースに最終形 follow_executor を確立
- 1セッション 50件で安定動作確認
- BrowserManager(action="follow") で chrome_profile_follow を使用

### Step 2: VM 上で Playwright を試験運用（5日）
- VM 内に Playwright + Chrome をセットアップ
- vm_follow_launcher.py に `--use-playwright` flag 追加
- pyautogui 版と並行運用、結果比較

### Step 3: pyautogui 版を deprecation（3日）
- VM 上 Playwright 版の安定性確認
- follow_rpa_vm.py の pyautogui 関数群を archive ディレクトリに移動
- vm_follow_launcher.py は default で Playwright 版を使用

### Step 4: 旧コード削除（最終承認後）
- `_BTN_X`, `BASE_W` などハードコード定数削除
- pyautogui import 削除
- ドキュメント更新

## 期待効果

| 項目 | 現状 | 移行後 |
|------|------|-------|
| 解像度依存 | あり (BASE_W hardcode) | なし (DOM-based) |
| verify 精度 | 9点ピンク判定 | DOM `.isFollowing` class check |
| rate_limit 検知 | 色判定 (誤検知あり) | DOM `[data-rate-limit]` の有無 |
| コード base | 2系統 (VM/HOST) | 1系統 (Playwright のみ) |
| 失敗率 | 11% (Phase A-3 後) | <5% (推定) |

## リスク

- VM 上 Playwright が想定通り動かない可能性 → Step 2 で試験運用必須
- CEO 第2アカウント禁止ルール (memory/rakuten_follow_account_rule.md) との整合性確認
- 移行中の運用停止リスク → 並行運用期間で吸収

## 完了基準

- 1週間連続で zero downtime 運用
- 失敗率 <10% を維持
- pyautogui import が follow_rpa_vm.py から消える
