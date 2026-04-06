# WP API 認証情報（5サイト）

> **用途**: CAP・サイバー 共有用
> **更新日**: 2026-04-06
> **管理者**: CEO

---

## サイト一覧

| # | サイト | URL | USER | APP_PASS |
|---|--------|-----|------|----------|
| 1 | taiyou-denryoku-hikaku | https://taiyou-denryoku-hikaku.com/ | taiyoukou | oSjq DkNq 1hA5 pS8Q 8L6g pWaV |
| 2 | solarpower-baikyaku | https://solarpower-baikyaku.info | adminadmin | TeCz ab6v oLr3 2G3n dfSw mWQi |
| 3 | eneuru | https://eneuru.com | eneuru_admin | rUTf cv8T GFBu usDY x23U bTgI |
| 4 | solar-kaitori | https://solar-kaitori.com | kVZvFbFV88dJ | LEli 9AXt aMCO djME trqg h9hI |
| 5 | （未取得） | - | - | - |

---

## WP REST API 接続方法

```python
import requests
from base64 import b64encode

WP_URL = "https://taiyou-denryoku-hikaku.com"
WP_USER = "taiyoukou"
WP_APP_PASS = "oSjq DkNq 1hA5 pS8Q 8L6g pWaV"

token = b64encode(f"{WP_USER}:{WP_APP_PASS}".encode()).decode()
headers = {"Authorization": f"Basic {token}"}

# 例: 記事一覧取得
res = requests.get(f"{WP_URL}/wp-json/wp/v2/posts", headers=headers)
```

---

## 各サイト .env 保管場所（サイバーPC）

```
solarworks-ai/
├── .env                         ← 本ファイルと同内容のメモあり
├── web-media/baikyaku/.env      ← [2] baikyaku 用
├── web-media/eneuru/.env        ← [3] eneuru 用
├── web-media/kaitori/.env       ← [4] kaitori 用
└── web-media/seo/.env           ← [1] taiyou 用
```

---

## 注意事項

- APP_PASS は WP アプリケーションパスワード（管理画面 > ユーザー > プロフィール で生成）
- CAP が独自 APP_PASS を発行する場合: 各サイト管理画面でユーザー追加 or 既存ユーザーで新規発行
- 本ファイルは private repo 内管理。外部共有禁止。
