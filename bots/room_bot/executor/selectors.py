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
