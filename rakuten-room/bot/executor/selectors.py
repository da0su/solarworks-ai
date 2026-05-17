"""ROOM BOT v2 - 楽天ROOM DOMセレクター定義

楽天ROOMのUI変更時はここだけ修正すればOK。
"""

# ============================================================
# ROOM mix/collect 画面（投稿編集画面）
# https://room.rakuten.co.jp/mix?itemcode=... で遷移した先
# ============================================================

# レビュー入力テキストエリア
REVIEW_TEXTAREA_SELECTORS = [
    'textarea[name="content"]',  # mix ページの投稿コメント欄
    'textarea[placeholder*="オススメ"]',
    'textarea[placeholder*="コメント"]',
    'textarea[placeholder*="感想"]',
    'textarea[placeholder*="おすすめ"]',
    'textarea[name*="comment"]',
    'textarea[name*="review"]',
    'textarea[name*="description"]',
    'div[contenteditable="true"]',
    'textarea',  # フォールバック: ページ内の最初のtextarea
]

# 投稿ボタン
SUBMIT_BUTTON_SELECTORS = [
    'button.collect-btn',  # mix ページの「完了」ボタン
    'button:has-text("完了")',
    'button:has-text("投稿する")',
    'button:has-text("コレ!")',
    'button:has-text("保存")',
    'button:has-text("投稿")',
    'button[type="submit"]',
    'input[type="submit"][value*="投稿"]',
]

# ============================================================
# 投稿成功 判定 (CEO 2026-05-17 真因確定 + Codex 14回目 review 反映)
# DOM 探索 (state/mix_page_screenshots/213903_04_after_submit_click.png) で発見:
# submit click 後、URL は変わらず (mix/collect のまま) ajax で完了画面表示.
# → 「コレ！完了!」テキスト or 「my ROOM を見る」link の出現を待つ.
#
# Codex 14回目 review 反映:
# - 全角「！」と半角「!」両対応必須 → Playwright regex で吸収
# - 汎用 'text=完了 !' / 'コレ完了' は誤検出 risk 高 → 削除
# - 第一優先: 'my ROOM を見る' link (高特異性) + href 取得して真の room_url にする
# ============================================================
# 第一優先 (高特異性 = 投稿完了画面にのみ存在する link)
# 完了画面の link button (header の常在 'my ROOM' link と誤検出しないよう厳格化)
# Codex 15→16回目 review 反映:
#   - 'a:text-is(...)' は Playwright で無効 (致命バグ) → 'a:has-text("...")' に修正
#   - 「my ROOM を見る」「my ROOMを見る」(空白あり/なし) 両対応
#   - ヘッダー常在は 'my ROOM' のみ → 「を見る」付きで区別される (textual)
POST_SUCCESS_LINK_SELECTOR = 'a:has-text("my ROOM を見る"), a:has-text("my ROOMを見る")'

# 第二優先 (Toast テキスト・全半角揺れに regex で対応)
# Playwright text selector (regex 形式)
POST_SUCCESS_TEXT_SELECTORS = [
    'text=/コレ[\\s!！]*完了[!！]/',  # 厳格 regex (全半角 ! 揺れ吸収)
]

# 投稿失敗 modal signature
POST_FAILURE_MODAL_SELECTORS = [
    'text=/Name\\s*は空白ではいけません/',
    'text=/エラーが発生しました/',
    'text=/投稿に失敗/',
    'text=/ログインしてください/',
]

# ============================================================
# 楽天市場 商品ページ上のROOM投稿リンク
# 「シェア」→「ROOMに投稿」で表示される
# ============================================================
ROOM_POST_LINK_SELECTORS = [
    'a[href*="room.rakuten.co.jp/mix"]',
    'a:has-text("ROOMに投稿")',
    '.susumeru-roomShareButton a',
    'a[href*="room.rakuten.co.jp"][href*="collect"]',
]

# ============================================================
# ログイン状態の確認
# ============================================================
LOGGED_IN_INDICATORS = [
    '.myroom-link',
    '[data-testid="user-menu"]',
    'a[href*="/my/"]',
    '.user-icon',
    '.header-mypage',
]

# ============================================================
# エラー・警告の検知（投稿後のみ使用。商品ページでは使わない）
# ============================================================
ERROR_TEXT_PATTERNS = [
    "操作が多すぎます",
    "しばらくお待ちください",
    "アカウントが一時停止",
    "ログインしてください",
    "セキュリティ確認",
]

# ROOM collect画面のエラー
COLLECT_ERROR_PATTERNS = [
    "この商品は投稿できません",
    "商品が見つかりません",
    "URLが正しくありません",
    "すでに投稿済み",
]

# collect画面 NG判定テキスト（404/NotFound系を含む厳密判定）
COLLECT_NG_TEXT_PATTERNS = [
    "404",
    "not found",
    "notfound",
    "ページが見つかりませんでした",
    "ページが見つかりません",
    "お探しのページは見つかりませんでした",
    "この商品は投稿できません",
    "商品が見つかりません",
    "URLが正しくありません",
    "すでに投稿済み",
    "エラーが発生しました",
    "アクセスできません",
    "ご指定のページが見つかりません",
    "存在しないページ",
]

# collect画面 OK判定セレクター（いずれか1つが見えればOK）
COLLECT_OK_SELECTORS = [
    'textarea[placeholder*="コメント"]',
    'textarea[placeholder*="感想"]',
    'textarea[placeholder*="おすすめ"]',
    'textarea[name*="comment"]',
    'textarea[name*="review"]',
    'textarea[name*="description"]',
    'div[contenteditable="true"]',
    'button:has-text("投稿する")',
    'button:has-text("コレ!")',
    'button:has-text("保存")',
]

# 投稿成功の確認
POST_SUCCESS_INDICATORS = [
    ':has-text("投稿しました")',
    ':has-text("投稿が完了")',
    ':has-text("コレ!しました")',
    '.post-complete',
]

# ============================================================
# ページ種別判定用ボタン/要素（デバッグ・ログ用）
# ============================================================
PAGE_ANALYSIS_SELECTORS = {
    "buttons": [
        "button",
        'a[role="button"]',
        'input[type="submit"]',
        'input[type="button"]',
    ],
    "links_room": [
        'a[href*="room.rakuten.co.jp"]',
    ],
}
