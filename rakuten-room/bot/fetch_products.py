"""楽天API商品取得 → posts.json 生成

楽天市場APIでキーワード検索し、ROOM投稿用の
title / url / image / comment の4項目だけのJSONを生成する。

使い方:
  python fetch_products.py "ステンレス 水筒"
  python fetch_products.py "ハンドクリーム" --count 5
  python fetch_products.py "キッチン 便利グッズ" --count 3 --output posts.json
"""

import argparse
import json
import sys
import urllib.request
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config


# 新ドメイン（2025年〜移行）
RAKUTEN_API_URL = "https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20220601"
# 旧ドメイン（フォールバック用）
RAKUTEN_API_URL_LEGACY = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"

# 仮コメント生成テンプレート（投稿パターンA: 発見系）
COMMENT_TEMPLATES = [
    "これ見つけて気になってます。{point}使い勝手よさそうだし、レビューも高評価。"
    "日常使いにちょうどいいサイズ感なのもポイント。\n\n#おすすめ #{genre} #暮らし #買ってよかった",

    "ずっと探してたやつ、やっと見つけた。{point}値段もちょうどいいし、"
    "デザインもシンプルで好み。迷ったけどポチりそう。\n\n#気になる #{genre} #楽天 #お気に入り",

    "口コミで評判よかったので気になってた。{point}"
    "実際レビュー読むと満足度高そう。試してみたい。\n\n#話題 #{genre} #人気 #レビュー高評価",
]


def fetch_rakuten_items(keyword: str, count: int = 3) -> list[dict]:
    """楽天市場APIで商品を検索する（新旧ドメイン両対応）"""
    app_id = config.RAKUTEN_APP_ID
    access_key = config.RAKUTEN_ACCESS_KEY
    if not app_id:
        print("エラー: RAKUTEN_APP_ID が未設定です。")
        print("  .env ファイルに RAKUTEN_APP_ID=xxxxx を追加してください。")
        sys.exit(1)

    base_params = {
        "applicationId": app_id,
        "keyword": keyword,
        "hits": min(count, 30),
        "sort": "-reviewCount",
        "imageFlag": 1,
        "format": "json",
    }
    if access_key:
        base_params["accessKey"] = access_key

    params = urllib.parse.urlencode(base_params)
    print(f"楽天API検索: 「{keyword}」 ({count}件)")

    # 新ドメイン → 旧ドメインの順で試行
    urls = [
        f"{RAKUTEN_API_URL}?{params}",
        f"{RAKUTEN_API_URL_LEGACY}?{params}",
    ]

    last_error = None
    for api_url in urls:
        domain = api_url.split("/")[2]
        print(f"  試行: {domain}")
        try:
            req = urllib.request.Request(api_url)
            with urllib.request.urlopen(req, timeout=15) as res:
                data = json.loads(res.read().decode("utf-8"))

            items = data.get("Items", [])
            if not items:
                print("商品が見つかりませんでした。キーワードを変えてみてください。")
                sys.exit(1)

            print(f"  → {len(items)}件 取得 (via {domain})")
            return items

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            print(f"  → {e.code} エラー: {body}")
            last_error = e
            continue
        except Exception as e:
            print(f"  → 通信エラー: {e}")
            last_error = e
            continue

    print(f"\n全てのAPIエンドポイントで失敗しました。")
    print("確認事項:")
    print("  1. RAKUTEN_APP_ID が正しいか")
    print("  2. RAKUTEN_ACCESS_KEY が設定されているか（新API必須）")
    print("  3. アプリが有効か（楽天Developers管理画面で確認）")
    sys.exit(1)


def to_post_data(items: list[dict], keyword: str) -> list[dict]:
    """楽天APIレスポンスをROOM投稿用の4項目に変換する"""
    posts = []
    genre = keyword.split()[0] if keyword else "おすすめ"

    for i, item_wrapper in enumerate(items):
        item = item_wrapper.get("Item", item_wrapper)

        # 画像URL取得（base64は使わない、URLのみ）
        image_url = ""
        medium_images = item.get("mediumImageUrls", [])
        if medium_images:
            first = medium_images[0]
            if isinstance(first, dict):
                image_url = first.get("imageUrl", "")
            elif isinstance(first, str):
                image_url = first

        # base64ブロック
        if image_url.startswith("data:"):
            image_url = ""

        # 商品の特徴をコメントに反映
        name = item.get("itemName", "")
        price = item.get("itemPrice", 0)
        point = f"{price:,}円で" if price else ""

        # テンプレートからコメント生成
        template = COMMENT_TEMPLATES[i % len(COMMENT_TEMPLATES)]
        comment = template.format(point=point, genre=genre)

        posts.append({
            "title": name[:80],
            "url": item.get("itemUrl", ""),
            "image": image_url,
            "comment": comment[:400],
        })

    return posts


def main():
    parser = argparse.ArgumentParser(
        description="楽天API商品取得 → ROOM投稿用JSON生成"
    )
    parser.add_argument("keyword", help="検索キーワード（例: 'ステンレス 水筒'）")
    parser.add_argument("--count", type=int, default=3, help="取得件数（デフォルト: 3）")
    parser.add_argument("--output", "-o", default="posts.json", help="出力ファイル名（デフォルト: posts.json）")

    args = parser.parse_args()

    # API取得
    items = fetch_rakuten_items(args.keyword, args.count)

    # 4項目に変換
    posts = to_post_data(items, args.keyword)

    # JSON保存
    output_path = Path(__file__).parent / args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)

    print(f"\n生成完了: {output_path}")
    print(f"  {len(posts)}件のデータを保存しました。")
    print()

    # プレビュー
    for i, p in enumerate(posts):
        print(f"  [{i+1}] {p['title'][:50]}")
        print(f"      URL:   {p['url'][:60]}...")
        print(f"      image: {'OK' if p['image'] else 'なし'}")
        print()

    print("次のステップ:")
    print(f"  python run.py batch --file {args.output} --count 1")


if __name__ == "__main__":
    main()
