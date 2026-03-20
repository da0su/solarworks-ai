"""ROOM BOT v2 - 投稿品質スコアラー

生成されたコメントの品質を0-100でスコアリングする。
75点未満は再生成を推奨。

チェック項目:
  - 文字数（80〜140文字が理想）
  - 絵文字の数（1〜3個が理想）
  - ハッシュタグ数（3〜5個が理想）
  - 改行構造（空行で区切られた3〜5ブロック）
  - 禁止表現チェック（宣伝臭い表現）
  - 日本語の自然さ（二重助詞など）
"""

import re

# スコアリング閾値
SCORE_THRESHOLD = 75

# 禁止・減点表現
BAD_EXPRESSIONS = [
    "絶対おすすめ",
    "マジで",
    "ぜひぜひ",
    "リンクから",
    "プロフィールから",
    "こちらから購入",
    "今すぐ",
    "限定セール",
    "在庫わずか",
    "急いで",
    "見逃せない",
]

# 二重助詞パターン
DOUBLE_PARTICLE_PATTERNS = [
    r"にに",
    r"がが",
    r"をを",
    r"でで",
    r"もも[^の]",
    r"はは[^は]",
]


def score_comment(comment: str, genre: str = "general") -> dict:
    """コメントの品質をスコアリングする

    Returns:
        dict: {
            "score": int (0-100),
            "details": list[str] (各チェック項目の結果),
            "pass": bool (score >= SCORE_THRESHOLD),
        }
    """
    details = []
    total = 0

    # テキスト部分（タグ行を除く）
    lines = comment.split("\n")
    text_lines = [l for l in lines if not l.startswith("#")]
    text_only = "\n".join(text_lines).strip()
    char_count = len(text_only.replace("\n", "").replace(" ", ""))

    tag_lines = [l for l in lines if l.startswith("#")]

    # 1. 文字数チェック（30点満点）
    if 80 <= char_count <= 140:
        total += 30
        details.append(f"文字数: {char_count}文字 [30/30]")
    elif 60 <= char_count < 80 or 140 < char_count <= 180:
        total += 20
        details.append(f"文字数: {char_count}文字 (やや範囲外) [20/30]")
    elif 40 <= char_count < 60 or 180 < char_count <= 200:
        total += 10
        details.append(f"文字数: {char_count}文字 (範囲外) [10/30]")
    else:
        details.append(f"文字数: {char_count}文字 (大幅に範囲外) [0/30]")

    # 2. 絵文字チェック（15点満点）
    emoji_count = len(re.findall(
        r"[\U0001F300-\U0001F9FF\U00002600-\U000027BF\U0000FE00-\U0000FE0F\U0001FA00-\U0001FAFF]",
        comment
    ))
    if 1 <= emoji_count <= 3:
        total += 15
        details.append(f"絵文字: {emoji_count}個 [15/15]")
    elif emoji_count == 0:
        total += 5
        details.append(f"絵文字: 0個 (少ない) [5/15]")
    elif emoji_count <= 5:
        total += 10
        details.append(f"絵文字: {emoji_count}個 (やや多い) [10/15]")
    else:
        total += 0
        details.append(f"絵文字: {emoji_count}個 (多すぎ) [0/15]")

    # 3. ハッシュタグチェック（15点満点）
    tag_count = len(tag_lines)
    if 3 <= tag_count <= 5:
        total += 15
        details.append(f"タグ: {tag_count}個 [15/15]")
    elif tag_count == 2 or tag_count == 6:
        total += 10
        details.append(f"タグ: {tag_count}個 (やや範囲外) [10/15]")
    elif tag_count >= 1:
        total += 5
        details.append(f"タグ: {tag_count}個 [5/15]")
    else:
        details.append(f"タグ: 0個 [0/15]")

    # 4. 構造チェック（15点満点）
    # 空行で区切られたブロック数
    blocks = [b.strip() for b in comment.split("\n\n") if b.strip()]
    if 3 <= len(blocks) <= 5:
        total += 15
        details.append(f"構造: {len(blocks)}ブロック [15/15]")
    elif len(blocks) == 2:
        total += 10
        details.append(f"構造: {len(blocks)}ブロック (やや少ない) [10/15]")
    else:
        total += 5
        details.append(f"構造: {len(blocks)}ブロック [5/15]")

    # 5. 禁止表現チェック（15点満点 → 減点方式）
    bad_found = []
    for expr in BAD_EXPRESSIONS:
        if expr in comment:
            bad_found.append(expr)
    penalty = len(bad_found) * 5
    bad_score = max(0, 15 - penalty)
    total += bad_score
    if bad_found:
        details.append(f"禁止表現: {', '.join(bad_found)} [-{penalty}] [{bad_score}/15]")
    else:
        details.append(f"禁止表現: なし [15/15]")

    # 6. 日本語自然さチェック（10点満点）
    grammar_issues = []
    for pattern in DOUBLE_PARTICLE_PATTERNS:
        if re.search(pattern, comment):
            grammar_issues.append(pattern)
    if not grammar_issues:
        total += 10
        details.append("文法: 問題なし [10/10]")
    else:
        total += 5
        details.append(f"文法: 二重助詞検出 [{', '.join(grammar_issues)}] [5/10]")

    return {
        "score": min(total, 100),
        "details": details,
        "pass": total >= SCORE_THRESHOLD,
    }


def score_and_regenerate(title: str, url: str, genre: str,
                          generate_fn, max_attempts: int = 3) -> tuple[str, dict]:
    """スコアが閾値以上になるまでコメントを再生成する

    Args:
        title: 商品タイトル
        url: 商品URL
        genre: ジャンル
        generate_fn: コメント生成関数 (title, url, genre) -> str
        max_attempts: 最大試行回数

    Returns:
        (comment, score_result) の tuple
    """
    best_comment = None
    best_score = None

    for _ in range(max_attempts):
        comment = generate_fn(title, url, genre)
        result = score_comment(comment, genre)

        if best_score is None or result["score"] > best_score["score"]:
            best_comment = comment
            best_score = result

        if result["pass"]:
            return comment, result

    # 閾値未満でも最高スコアのものを返す
    return best_comment, best_score
