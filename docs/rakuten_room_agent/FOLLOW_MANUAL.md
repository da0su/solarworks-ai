# 楽天ROOM フォローBOT マニュアル

**バージョン**: v4.1
**更新日**: 2026-03-09

---

## 運用方針

- BOTがフォローを**完全自動実行**する。CEO承認は不要
- CEOは監視と例外対応のみ

---

## 1. 日次件数

| 項目 | 値 |
|------|-----|
| 日次フォロー数 | **150〜250件**（ランダム） |
| セッション分割 | **50件 × 4セッション** |
| 日次リセット | 午前0時 |

毎日同じ件数にしない（BOT対策）。日次の件数は150〜250の間でランダムに決定する。

---

## 2. セッション分散ルール

1日のフォローを**4セッションに分散**して実行する。

| セッション | 件数 | 時間帯 |
|-----------|------|--------|
| セッション1 | 最大50件 | いつでもOK |
| セッション2 | 最大50件 | いつでもOK |
| セッション3 | 最大50件 | いつでもOK |
| セッション4 | 最大50件 | いつでもOK |

### セッション内休憩
- **10〜15件ごと**にランダム休憩を入れる
- 休憩時間: ランダム（短時間集中を防ぐ）

### セッション間休憩
- セッション間にランダム休憩を挟む
- 短時間に4セッション連続実行しない

---

## 3. フォロー実行フロー

```
recommendUsersページを開く
    ↓
スクロールしてユーザー読み込み
    ↓
上から順にユーザーを確認
    ↓
┌─────────────────────────────┐
│ フォロー済み? → スキップ     │
│ 未フォロー?  → クリック      │
│            → 成功/失敗を記録 │
└─────────────┬───────────────┘
              ↓
    10〜15件ごとにランダム休憩
              ↓
    success_count >= target_count ?
    YES → 即停止
    NO  → 次のユーザーへ
```

---

## 4. 件数制御ルール（必須・最重要）

```
follow_target_count = 150〜250（日次ランダム）
follow_session_max = 50（1セッションの上限）
```

### 基本ルール
1. `target_count` で指定した件数で**必ず停止**する
2. 1セッション**最大50件**で停止する
3. 既フォロー済みユーザーはカウントしない（スキップ扱い）
4. 実際に新規フォロー成功した件数（`success_count`）だけをカウントする
5. 上限に達したら**即停止**する（1件たりとも超えない）
6. ログに以下を保存する:
   - `target_count` — 目標件数
   - `success_count` — 成功件数
   - `skip_count` — スキップ件数
   - `error_count` — エラー件数

### カウントロジック
```
for each user:
    if success_count >= session_target (max 50):
        STOP  ← ループの最初で必ず確認
    if already_following:
        skip_count++
        continue
    click follow button
    if success:
        success_count++
        if success_count % random(10,15) == 0:
            random_rest()  ← ランダム休憩
    else:
        error_count++
```

### 間隔ルール
- フォロー間隔: **ランダム化**（固定間隔にしない＝BOT対策）
- セッション内休憩: **10〜15件ごと**にランダム休憩
- セッション間休憩: ランダム休憩

---

## 5. 実行ページ

- **起点**: `https://room.rakuten.co.jp/discover/recommendUsers`
- スクロールで追加ユーザーを読み込む
- 全ユーザーがフォロー済みの場合はページリロードで新規取得を試みる

---

## 6. DOM技術メモ

### フォローボタンの構造
```html
<div class="border-button ng-scope" ng-click="discover.toggleFollow(user)">
    <span class="icon-follow ng-scope"></span>       ← 未フォロー
    <span class="active icon-follow ng-scope"></span> ← フォロー済み
</div>
```

### クリックターゲット
- **正**: `div.border-button` （親要素 — ng-clickがここにある）
- **誤**: `span.icon-follow` （子要素 — Angularイベントが発火しない）

### フォロー状態の判定
- `span.icon-follow.active` → フォロー済み
- `span.icon-follow` （activeなし） → 未フォロー

---

## 7. ログ保存

### フォローログ（FOLLOW_LOG.json）

保存先: `05_CONTENT/rakuten_room/history/FOLLOW_LOG.json`

```json
{
    "sessions": [
        {
            "date": "2026-03-10",
            "session": 1,
            "target_count": 50,
            "success_count": 48,
            "skip_count": 32,
            "error_count": 2,
            "started_at": "2026-03-10T09:00:00",
            "finished_at": "2026-03-10T09:20:00",
            "stop_reason": "target_reached",
            "rest_breaks": 4,
            "details": [
                { "index": 1, "userName": "user1", "status": "success" },
                { "index": 2, "userName": "user2", "status": "error", "reason": "state_unchanged" }
            ]
        }
    ]
}
```

---

## 8. エラーハンドリング

| エラー | 対処 |
|--------|------|
| フォローボタンが見つからない | スキップして次へ |
| クリック後に状態変化なし | error_countに記録、次へ |
| ページ上の全ユーザーがフォロー済み | ページリロード or stop |
| 「操作が多すぎます」警告 | **即停止**。CEOに報告 |
| セッション切れ | **即停止**。CEOに報告 |

---

## 9. 段階テスト

| テスト | フォロー数/日 | セッション |
|--------|---------------|-----------|
| Test A | 50件 | 50件×1 |
| Test B | 100件 | 50件×2 |
| Test C | 150〜250件 | 50件×4 |
