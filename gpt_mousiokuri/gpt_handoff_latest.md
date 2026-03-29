# GPT申し送り - 2026-03-29 16:11 JST

## 1. Company Direction
  CEO→CAP（代表COO）→cyber 3層自動運用。楽天ROOM自動投稿+コイン仕入れリサーチ。

## 2. Progress vs Objective
  - 楽天ROOM: health=CRITICAL / pool=0件
  - コイン: DB=0件
  - eBay: 候補21件 / 最終検索=2026-03-28 17:24
  - 定時チェック: 07:30/12:30/18:30 定時チェック稼働中
    07:30: ⏳ not_fired
    12:30: ⏳ not_fired
    18:30: ⏳ not_fired

## 3. Current Issues
  - [unknown] Unknown task: rakuten-status  (2026-03-29T06:01)
  - [rakuten-status] Unknown task: rakuten-status  (2026-03-29T06:01)
  - [unknown] Unknown task: rakuten-status  (2026-03-29T06:00)
  - [rakuten-status] Unknown task: rakuten-status  (2026-03-29T06:00)

## 4. Next Priority
  - cap/cyber watch 常時起動維持
  - daily-check 定時発火 監視 (schedule_state.json 確認)
  - ebay-review 承認待ち (21件)

## 5. Risk / Bottlenecks
  - 楽天ROOM health=CRITICAL ⚠️
  - schedule_state.json 未生成の場合は cap watch 再起動が必要

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
  - ebay-review 承認待ち (21件): python slack_bridge.py approve --task ebay-review --by cap
  - 楽天ROOM health=CRITICAL: python run.py health で詳細確認
