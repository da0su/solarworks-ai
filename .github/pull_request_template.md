# PR Template (CEO 5/20 + Codex 29回目 #12 反映)

## サマリ (1-3 行)

<!-- 何を変えたか・なぜ変えたか. 検証ロジック変更は明示せよ -->

## 想定失敗ケース

<!-- この change で壊れる/退行するケースを必ず列挙. 「無い」と言い切らないこと -->

-

## 検証手順

<!-- 再現可能な test command. unit/integration/manual を明示 -->

- [ ] `python <test_file>` で all pass
- [ ] dry-run で確認 (該当する場合)
- [ ] 本番反映前に DB backup (該当する場合)

## ロールバック手順

<!-- 万一 false success / 信頼性問題が出た時の即時切り戻し手順 -->

```bash
git revert <this_commit_sha>
git push origin main
```

## CEO 信頼性 ブロッカー チェック (必須・該当に ✅)

- [ ] 「URL 残留 = 失敗」判定の削除/緩和 → **CEO 承認必須** (5/12 false success 教訓)
- [ ] DOM 状態確認の削除/緩和 → **CEO 承認必須**
- [ ] 既存 fix の revert / 上書き → **Codex review APPROVE 必須**
- [ ] FOLLOW 数値報告 ロジック変更 → **Codex review APPROVE 必須**
- [ ] 上記いずれにも該当しない (通常 change)

## Codex Review

```bash
python ops/codex_review.py --commit HEAD --context "<change の背景 1-2 行>"
```

- [ ] Codex verdict = APPROVE (REVIEW_NEEDED は CEO 確認後 push)
- [ ] REJECT の場合は push 禁止 (改修して再 review)

## §6 絶対禁止 確認 (CLAUDE.md より)

- [ ] false success 再発防止 (5/12-5/17 6日間教訓を理解している)
- [ ] URL 残留 = 失敗 判定の削除/緩和 を含まない (CEO「投稿はずっと 0 のはず」教訓)
- [ ] 「コメント省略」許容を含まない
- [ ] 削除機能 実装を含まない (CEO「削除する必要はない」)
