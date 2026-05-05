# VM Chrome 楽天ROOM再ログイン手順書

**作成日**: 2026-05-05  
**対象**: VirtualBox VM "RoomBot" 内 Chrome の楽天ROOMセッション失効時

---

## 目的

`patrol_hourly.py` が `<!channel> 【パトロール緊急】楽天ROOMログイン失効を検知` Slack通知を送ってきた場合の復旧手順。VM Chrome の楽天ROOMセッションcookieが失効した状態。

## 検知契機（自動）

`follow_rpa_vm.py` の `check_login_status()`（2026-05-05実装）が、Chrome起動直後に `room.rakuten.co.jp/feed` へnavigate→URL bar を確認:
- `room.rakuten.co.jp/feed*` → ログイン中（OK・bot継続）
- `login.rakuten.co.jp` / `/nid/` / `myinfo` 系URL → **ログイン失効**
- 失効時：bot abort + `\\VBOXSVR\share\login_expired_flag.json` 生成
- patrol が次回 :00/:15/:30/:45 で flag を検出 → CEO Slack通知

## 復旧手順（CEO作業）

### 0. VirtualBox を開いて RoomBot VMコンソールを表示
- VM が起動していなければ `VBoxManage startvm RoomBot` でも自動起動済み
- パトロール 11:09 検知後の自動起動は実装済み（patrol_hourly.py の `_vm_auto_recover`）

### 1. VM 内 Chrome を最大化
- タスクバーの Chrome アイコンをクリック
- ウィンドウが最小化されていれば最大化（Win+↑）

### 2. Chrome アドレスバーに `room.rakuten.co.jp` を入力 → Enter
- 「ログイン」ボタンが画面右上または中央に表示される

### 3. 「ログイン」ボタンをクリック
- 楽天ID 入力画面へ

### 4. 楽天ID + パスワード入力 → ログイン
- 必要なら2要素認証コード入力

### 5. ログイン後の確認
- アドレスバーに `room.rakuten.co.jp/feed` を直接入力 → Enter
- 自分のフィードページが表示されればOK
  - 表示例: 「ガイド | 楽天市場」ナビ + 自分のフォロー中ユーザーの投稿一覧
- もし「ROOMをはじめる」ボタンが出る → ログイン失敗（最初からやり直し）

### 6. cookie 永続化確認
- `chrome://settings/cookies/detail?site=room.rakuten.co.jp` を開く
- 以下のcookieが存在していること:
  - `Rses` （セッション）
  - `Raut` （認証トークン）
  - `_ra` （recognition）
- Chrome のプロファイルに永続化されているため再起動しても保持される

### 7. flag ファイル削除（任意・cookieが復活すれば不要）
- HOST側で実行（ログイン成功後の patrol が flag を再生成しないことを確認した上で）:
  ```
  Remove-Item C:\Users\infoa\Documents\solarworks-ai\rakuten-room\bot\executor\login_expired_flag.json -ErrorAction SilentlyContinue
  ```
- 次回パトロールで login_status="ok" になれば自動的に Slack通知は止まる（2hスロットル）

### 8. bot 再起動
- HOST cmd で:
  ```
  python C:\Users\infoa\Documents\solarworks-ai\ops\vm_follow_launcher.py --force --limit 100
  ```
- 30分以内に follow_rpa_log.json mtime が更新 + success>0 を確認

## トラブルシューティング

| 症状 | 対処 |
|------|------|
| 楽天ID入力しても進まない | Chromeが新規プロファイルで起動している可能性。VMの `C:\Users\cyber\AppData\Local\Google\Chrome\User Data` を確認 |
| cookieが保存されない | Chromeの「Cookieとサイトデータ」設定がブロック設定になっていないか確認 |
| 2要素認証が来ない | 楽天モバイルアプリ通知 / SMSいずれか確認。それでも来なければ楽天サポート |
| ログインしても /feed で「ROOMをはじめる」が出る | アカウント自体がROOM未利用扱い。CEO要確認 |
| login_expired_flag が消えない | flag ファイルを手動削除後、`python ops/patrol_hourly.py` 手動実行で確認 |

## 関連ファイル

- `rakuten-room/bot/executor/follow_rpa_vm.py:check_login_status()` — 検知ロジック
- `rakuten-room/bot/executor/login_expired_flag.json` — VirtualBox shared folder マッピング
- `ops/patrol_hourly.py` — flag 検出 + Slack通知
- `state/follow_runtime_state.json` — SSOT (login_status を含む統合 state)

## 認証情報の取り扱い

- **このリポジトリには楽天ID/パスワードを保存しない**
- credentials は CEO のみが保持
- 自動再ログインは将来的に検討するが、2要素認証必須のため当面は手動対応
