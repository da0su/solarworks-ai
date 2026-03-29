# GPT申し送り - 2026-03-29 17:27 JST

## 1. Company Direction
  CEO→CAP（代表COO）→cyber 3層自動運用。楽天ROOM自動投稿+コイン仕入れリサーチ。

## 2. Progress vs Objective
  - 楽天ROOM: health=CRITICAL / pool=0件
  - コイン: DB=0件
  - eBay: 候補21件 (未承認0件) / 最終検索=2026-03-28 17:24
  - 定時チェック: 07:30/12:30/18:30 定時チェック稼働中
    07:30: 🔄 running
    12:30: 🔄 running
    18:30: ⏳ not_fired

## 3. Current Issues
  (なし)

## 4. Next Priority
  - cap/cyber watch 常時起動維持
  - daily-check 定時発火 監視 (schedule_state.json 確認)
  - ebay-search 次回実行予定

## 5. Risk / Bottlenecks
  - 楽天ROOM health=CRITICAL ⚠️
  - スケジュール管理 正常

## 6. Operational Knowledge
  - Slack 2500字制限: slim payload のみ送信、詳細は*_latest.jsonを参照
  - 重複発火防止: schedule_state.json の status=done で制御
  - セッション引継ぎ: このファイル(daily_handoff.json)を読む
  - cyberは git pull 後に watch 再起動で最新コードを読み込む

## 7. Behavioral Notes
  - CEO: batを押すだけ。日次は #ceo-room を確認。判断事項のみCAPに指示。
  - CAP: 代表COO。watch常時起動。daily-handoff で日次申し送り生成。
  - cyber: 実処理担当。watch常時起動。git pullで最新コード維持。ebay-search実行役。

## 8. Decision Required
  - 楽天ROOM health=CRITICAL: python run.py health で詳細確認
