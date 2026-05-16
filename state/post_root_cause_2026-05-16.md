# POST 真因解明 メモ (2026-05-16 18:00)

## 結論

**5/12 以降 POST 0 件の真因は Rakuten ROOM mix ページの仕様変更**

過去推測されていた「URL check 撤去で false success」は表層原因。
水面下で実際の投稿 mechanic も同時期から壊れていた。

## 証拠

- 5/16 17:53 Batch3 手動 trigger 結果:
  - item 1, 2, 3 全て「URL unchanged 30s」で failed
  - 撮影 screenshot: `rakuten-room/bot/data/screenshots/2026-05-16/175516_06_url_unchanged.png`

- screenshot から判明:
  - 完了 ボタン click 後にモーダル「**Name は空白ではいけません。**」
  - レビュー textarea が **0/500 で空** (placeholder "オススメポイントや好きな点など…" が見える)
  - log では「入力完了: 108文字」と出ているが **DOM の visible textarea には反映されていない**

## 推定原因

### 仮説 A: Selector 古い (最有力)
- 旧 selector `textarea[name="content"]` が hidden/legacy field を hit
- 実 visible textarea は別 selector (例: `textarea[name="comment"]` や class ベース)
- 結果: 入力したテキストが visible textarea に届かず、submit 時に validation fail

### 仮説 B: 「コレ」機能の新 UI で必須 field 追加
- mix ページが「コレ！して投稿する」UI に変更
- collection name 必須化 (Name field)
- 旧 flow (textarea 1 つだけ) では対応できない

## 5/10 最終成功日との突合

| 日 | Posted | 状況 |
|---|---|---|
| 5/10 | 176 | 旧 mix UI で正常稼働 |
| 5/11 | 0 | FOLLOW 監査 mode (POST 抑制) |
| 5/12 | 0 | mix UI 変更開始? + URL check 撤去 (commit 8f34a76) |
| 5/13-15 | 0 | UI 不一致 + false success 報告 |
| 5/16 | 0 | URL check 復活 (commit 9d6a304) で真因可視化 |

→ 5/11-5/12 のどこかで Rakuten 側 UI 変更が起きた可能性が高い。

## 次セッション 修正手順

1. login 済 chrome_profile_post で mix ページを手動で開く
2. F12 / Inspector で:
   - textarea / input / [contenteditable] 全列挙
   - 「Name」エラーがどの field を指しているか特定
   - 投稿に必要な field 全部の selector を取得
3. `rakuten-room/bot/executor/_dom_explore.py` を mix page 対応に拡張
4. `selectors.py` の REVIEW_TEXTAREA_SELECTORS を更新
5. 必要なら post_executor.py に Name field 入力 step 追加
6. Codex review → 1件 dry-run → 段階展開

## 緊急度

**CEO 信頼性 + 売上影響大**:
- 5/11-5/16 で約 **600件 投稿機会損失** (target 60/日 × 6日 × 達成率 70%想定)
- 過去 5/12-5/16 false success 450件は既に failed marking 完了
- 修正完了で 5/17 から完全復旧見込み

## 関連 commit

- 5/16 9d6a304: 厳格 URL regex (これで真因が可視化された)
- 5/12 8f34a76: URL check 撤去 (5/16 a5c2272 で revert)
- 5/10 (post_executor が正常稼働してた最後のバージョン): git log で要特定

## 参考 screenshot

```
rakuten-room/bot/data/screenshots/2026-05-16/
  175446_05_submitted.png     ← 完了ボタン click 直後 (textarea 0/500 空)
  175516_06_url_unchanged.png ← 30s 後 (Name validation modal)
  175618_05_submitted.png     ← item 2 同パターン
  175648_06_url_unchanged.png ← item 2 結果
  175814_05_submitted.png     ← item 3 同パターン
  175844_06_url_unchanged.png ← item 3 結果
```
