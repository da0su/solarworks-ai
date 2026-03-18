# 意思決定: 事業別フォルダ構成への移行

- 日付: 2026-03-18
- 決定者: CEO + CTO
- 実行者: Desktop A COO

## 背景

- 事業数の増加により、旧部署コード構造（00-09）では管理が困難に
- Claude Code 2アカウント体制（Desktop A + ノート型）で並行開発を開始
- 同一フォルダへの同時アクセスを防ぐため、事業単位での排他占有が必要

## 決定内容

1. **事業別トップレベルフォルダ**に移行
   - coin, rakuten-room, china-oem, export-ec, web-media, apps, video-lab
2. **横断フォルダ**を3つに分離
   - ops（運用基盤）、shared（共通ライブラリ）、docs（ドキュメント）
3. **旧構造（00-09）はarchiveへ段階的に移行**
4. **占有ルール**: 事業フォルダ=排他占有、ops/shared=契約A優先、docs=両方OK

## 事業一覧（経営前提）

- コイン
- 楽天ROOM
- 中国輸入OEM物販（Amazon / 楽天 / その他EC）
- 輸出物販
- WEB製作（サイト修正・自動投稿・アフィリエイト・アドセンス）
- 恋愛相談アプリ
- 占いアプリ
- 秘書管理ツール（ops/shared に集約）
- 動画ラボ（Instagram / TikTok / 複数アカウント前提）

## Git運用

- main: 安定版
- feature/事業名-xxx: 作業ブランチ
- 同一事業フォルダを2ブランチで同時編集しない
- shared/ の変更は必ずPR経由

## 未実施（次アクション）

- [ ] 既存コードの物理移行（08_AUTOMATION → rakuten-room/bot 等）
- [ ] 旧構造（00-09）のarchive化
- [ ] CLAUDE.md のパス更新
- [ ] Notion 事業一覧DB作成
