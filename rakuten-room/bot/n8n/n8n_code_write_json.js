// ============================================================
// n8n Code node: JSON書出し（一時ファイルに保存）
// ============================================================
// 配置: payload整形ノードの直後
// 目的: batch用JSONファイルを生成して Execute Command に渡す
// ============================================================

const fs = require('fs');
const os = require('os');
const path = require('path');

// 全アイテムを配列にまとめる
const posts = $input.all().map(item => item.json);

// 一時ファイルのパス
// Windows: C:\Users\ユーザー\AppData\Local\Temp\room_posts.json
// Linux:   /tmp/room_posts.json
const tmpDir = os.tmpdir();
const filePath = path.join(tmpDir, 'room_posts.json');

// JSON書出し（UTF-8）
fs.writeFileSync(filePath, JSON.stringify(posts, null, 2), 'utf-8');

// 次のノード（Execute Command）にファイルパスを渡す
return [{
  json: {
    filePath: filePath,
    postCount: posts.length,
    message: `${posts.length}件のデータを ${filePath} に保存しました`
  }
}];
