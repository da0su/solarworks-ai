# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — 法令遵守版 ページビルダー (CEO 確定 2026-05-18)

v2 -> v3 主要変更:
- すべての section に画像 (WP メディアライブラリ ID 経由) を反映
- お客様の声 (CUSTOMER VOICE) セクション 完全削除
- 「全国 送料無料 / 1年メーカー保証 / 国内サポート対応」 benefits リスト 削除
- 「税込・送料無料」 -> 「メーカー希望小売価格 + 注記」
- 「マッサージ / 血流を促す」 等 薬機法 NG 表現 全置換
- 連絡先メール: support@kapibaran.com -> info@kapibaran.com (FAQ 文面も改訂)
- 商品ページ: 機器分類 (リラクゼーション機器) 明記 / 販売店規定に準じる注記
- 商品ページ: 「販売店でご確認ください」support-note を benefits の代わりに追加
- カテゴリーカード / 商品カード / 商品詳細 ギャラリーは <img src> で実画像表示
- ヒーローは背景画像 + overlay rgba(43,34,24,0.35) で視認性確保

CALL シグネチャ:
    media_map: dict[str, int] - キー (HERO/CAT_FOOT/PROD_FC_BE/...) -> attachment ID
    media_urls: dict[str, str] - キー -> source_url (URL)
"""
from __future__ import annotations
from typing import Dict, Any

from products_v3 import (
    PRODUCTS, COMING_SOON_CATEGORIES, COLOR_HEX, COLOR_JP,
    get_all_categories, get_product_by_slug,
)


CONTACT_EMAIL = "info@kapibaran.com"


def html_block(inner: str) -> str:
    return f"<!-- wp:html -->\n{inner.strip()}\n<!-- /wp:html -->"


def _img_url(media_urls: Dict[str, str], key: str, fallback: str = "") -> str:
    return media_urls.get(key, fallback)


# ===========================================================
# TOP page
# ===========================================================
def build_top(media_urls: Dict[str, str]):
    parts = []

    hero_url = _img_url(media_urls, "HERO")

    # ---- Hero (背景画像 + overlay) ----
    parts.append(html_block(f"""
<section class="kbv2-hero kbv3-hero" style="background-image: url('{hero_url}');">
  <div class="kbv3-hero__overlay"></div>
  <div class="kbv2-hero__inner kbv3-hero__inner">
    <p class="kbv2-hero__en">Upgrade Your Everyday.</p>
    <h1 class="kbv2-hero__jp">動き、整え、続けていく。</h1>
    <p class="kbv2-hero__lead">KAPIBARAN は、暮らしの当たり前を、もうひとつ上のあたりまえに変えるためのブランドです。<br>
      フットケアとホームフィットネスから、日々の体を整える道具をお届けします。</p>
    <a href="/products/" class="kbv2-btn kbv2-btn--primary">プロダクトを見る</a>
  </div>
</section>
"""))

    # ---- CONCEPT ----
    parts.append(html_block("""
<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow kbv2-text-center">
    <p class="kbv2-eyebrow">CONCEPT</p>
    <h2 class="kbv2-h2">暮らしと体を、ちいさく上質に。</h2>
    <p class="kbv2-lead">派手さよりも、長く愛せる確かさ。流行よりも、毎日の心地よさ。<br>
      KAPIBARAN は、暮らしと体に小さな上質を届けるための、<br>
      日本のプレミアム D2C ブランドです。</p>
  </div>
</section>
"""))

    # ---- CATEGORIES (4 cards with images) ----
    cats = get_all_categories()
    cards_html = ""
    for c in cats:
        img_url = _img_url(media_urls, c["image_key"])
        badge = '<span class="kbv2-cat-card__badge">Coming Soon</span>' if c["status"] == "coming_soon" else ""
        muted = "kbv2-cat-card--muted" if c["status"] == "coming_soon" else ""
        if not img_url:
            # 画像なければセクション非表示原則だが、4 カードのうち欠けたら個別に skip
            continue
        cards_html += f"""
    <a href="{c['link']}" class="kbv2-cat-card kbv3-cat-card {muted}">
      <div class="kbv3-cat-card__img" style="background-image: url('{img_url}');">{badge}</div>
      <p class="kbv2-cat-card__en">{c['en']}</p>
      <p class="kbv2-cat-card__jp">{c['jp']}</p>
      <p class="kbv2-cat-card__sub">{c['subtitle']}</p>
    </a>"""

    if cards_html:
        parts.append(html_block(f"""
<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">PRODUCT CATEGORIES</p>
      <h2 class="kbv2-h2">4 つの領域で、暮らしを整える。</h2>
    </div>
    <div class="kbv2-cat-grid">{cards_html}
    </div>
    <p class="kbv2-text-center kbv2-mt-l">
      <span class="kbv2-sub">※ ボディケア / ボディシェイピングは Coming Soon</span>
    </p>
  </div>
</section>
"""))

    # ---- NEW ARRIVALS (Real 2 products with images + MSRP) ----
    arr_html = ""
    for p in PRODUCTS:
        main_img = _img_url(media_urls, p["main_image_key"])
        if not main_img:
            continue
        chips = "".join(
            f'<span class="kbv2-color-chip" style="background:{COLOR_HEX[c]};" title="{COLOR_JP[c]}"></span>'
            for c in p["colors"]
        )
        arr_html += f"""
    <a href="/products/{p['slug']}/" class="kbv2-prod-card kbv3-prod-card">
      <div class="kbv3-prod-card__img" style="background-image: url('{main_img}');"></div>
      <div class="kbv2-prod-card__body">
        <p class="kbv2-prod-card__cat">{p['category_en']}</p>
        <p class="kbv2-prod-card__name">{p['full_name']}</p>
        <p class="kbv3-prod-card__msrp-label">{p['msrp_label']}</p>
        <p class="kbv2-prod-card__price">{p['price_display']}（税込）</p>
        <div class="kbv2-prod-card__colors">{chips}</div>
      </div>
    </a>"""
    if arr_html:
        parts.append(html_block(f"""
<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">NOW AVAILABLE</p>
      <h2 class="kbv2-h2-en">Products</h2>
      <p class="kbv2-lead kbv2-text-center">いま、お届けしている商品。</p>
    </div>
    <div class="kbv2-prod-grid">{arr_html}
    </div>
    <p class="kbv3-arrivals-note kbv2-text-center kbv2-mt-l">
      ※実際の販売価格・送料・在庫・保証等の詳細は、各販売店（Amazon・楽天市場・Yahoo!ショッピング）の商品ページをご確認ください。
    </p>
    <p class="kbv2-text-center kbv2-mt-xl">
      <a href="/products/" class="kbv2-btn kbv2-btn--ghost">すべての商品を見る →</a>
    </p>
  </div>
</section>
"""))

    # ---- JOURNAL strip ----
    parts.append(html_block("""
<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">JOURNAL</p>
      <h2 class="kbv2-h2">読みもの</h2>
    </div>
    <p class="kbv2-text-center kbv2-mt-l">
      <a href="/category/journal/" class="kbv2-btn kbv2-btn--ghost">ジャーナルを読む →</a>
    </p>
  </div>
</section>
"""))

    # ---- BRAND VALUES ----
    parts.append(html_block("""
<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">BRAND VALUES</p>
      <h2 class="kbv2-h2">わたしたちが、大切にしていること。</h2>
    </div>
    <div class="kbv2-values">
      <div class="kbv2-value">
        <div class="kbv2-value__icon">01</div>
        <h3 class="kbv2-value__title">日本品質の安心感</h3>
        <p class="kbv2-value__body">素材選びから検品まで、ひとつひとつに目を行き届かせる。日本のものづくりが培ってきた誠実さを、商品の隅々まで。</p>
      </div>
      <div class="kbv2-value">
        <div class="kbv2-value__icon">02</div>
        <h3 class="kbv2-value__title">続けたくなる心地よさ</h3>
        <p class="kbv2-value__body">毎日触れるものだからこそ、心地よさを徹底的に。質感・重さ・操作性まで、毎日の所作になじむ設計を。</p>
      </div>
      <div class="kbv2-value">
        <div class="kbv2-value__icon">03</div>
        <h3 class="kbv2-value__title">暮らしに馴染むデザイン</h3>
        <p class="kbv2-value__body">主張しすぎず、けれど確かにそこにある。10 年後の暮らしにも自然にとけ込むデザインを目指して。</p>
      </div>
    </div>
  </div>
</section>
"""))

    # ---- CTA ----
    parts.append(html_block(f"""
<section class="kbv2-cta">
  <div class="kbv2-container kbv2-text-center">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">SUPPORT</p>
    <h2 class="kbv2-h2 kbv2-h2--light">お問い合わせ・修理のご相談</h2>
    <p class="kbv2-cta__lead">プロダクトに関するご質問、サポート、修理のご相談はこちらから。<br>
      <span class="kbv3-cta__email">{CONTACT_EMAIL}</span>
    </p>
    <a href="/contact/" class="kbv2-btn kbv2-btn--accent">お問い合わせフォームへ</a>
  </div>
</section>
"""))

    return "\n\n".join(parts)


# ===========================================================
# About page
# ===========================================================
def build_about(media_urls: Dict[str, str]):
    about_url = _img_url(media_urls, "ABOUT")
    hero_style = f"background-image: url('{about_url}');" if about_url else ""
    hero_overlay = '<div class="kbv3-hero__overlay"></div>' if about_url else ""

    return html_block(f"""
<section class="kbv2-page-hero kbv3-page-hero" style="{hero_style}">
  {hero_overlay}
  <div class="kbv2-page-hero__inner">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">BRAND STORY</p>
    <h1 class="kbv2-h1 kbv2-h1--light">About KAPIBARAN</h1>
    <p class="kbv2-page-hero__sub">わたしたちのこと。</p>
  </div>
</section>

<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow">
    <h2 class="kbv2-h2 kbv2-text-center">暮らしと体を、ちいさく上質に。</h2>
    <p class="kbv2-lead kbv2-text-center kbv2-mt-l">
      KAPIBARAN（カピバラン）は「毎日を、ちょっといいに。」を理念に掲げる
      日本のプレミアム D2C ブランドです。<br><br>
      派手なメッセージではなく、ふと触れた瞬間に「いい」と感じる。
      そんなプロダクトをひとつずつ世に送り出していきます。
    </p>
  </div>
</section>

<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container kbv2-narrow">
    <div class="kbv2-timeline">
      <div class="kbv2-timeline__item"><span class="kbv2-timeline__year">2025</span><span class="kbv2-timeline__txt">ブランド構想</span></div>
      <div class="kbv2-timeline__item"><span class="kbv2-timeline__year">2026</span><span class="kbv2-timeline__txt">第一弾 5 SKU を発売（フットケア家電 / スマートトレッドミル）</span></div>
      <div class="kbv2-timeline__item"><span class="kbv2-timeline__year">2026</span><span class="kbv2-timeline__txt">ブランドサイト公開</span></div>
      <div class="kbv2-timeline__item"><span class="kbv2-timeline__year">2026〜</span><span class="kbv2-timeline__txt">ボディケア / ボディシェイピング 領域へ拡張</span></div>
    </div>
  </div>
</section>

<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">BRAND VALUES</p>
      <h2 class="kbv2-h2">わたしたちが、大切にしていること。</h2>
    </div>
    <div class="kbv2-values">
      <div class="kbv2-value">
        <div class="kbv2-value__icon">01</div>
        <h3 class="kbv2-value__title">日本品質の安心感</h3>
        <p class="kbv2-value__body">素材選びから検品まで、ひとつひとつに目を行き届かせる。日本のものづくりが培ってきた誠実さを、商品の隅々まで。</p>
      </div>
      <div class="kbv2-value">
        <div class="kbv2-value__icon">02</div>
        <h3 class="kbv2-value__title">続けたくなる心地よさ</h3>
        <p class="kbv2-value__body">毎日触れるものだからこそ、心地よさを徹底的に。質感・重さ・操作性まで、毎日の所作になじむ設計を。</p>
      </div>
      <div class="kbv2-value">
        <div class="kbv2-value__icon">03</div>
        <h3 class="kbv2-value__title">暮らしに馴染むデザイン</h3>
        <p class="kbv2-value__body">主張しすぎず、けれど確かにそこにある。10 年後の暮らしにも自然にとけ込むデザインを目指して。</p>
      </div>
    </div>
  </div>
</section>

<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container kbv2-narrow">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">CRAFT</p>
      <h2 class="kbv2-h2 kbv2-text-center">ものづくりのこと。</h2>
    </div>
    <p class="kbv2-lead">
      KAPIBARAN は「足元から、暮らしを整える」ことから始めました。<br>
      フットケアという、もっとも身近で見過ごされがちな領域から、<br>
      ホームフィットネスへ、そして体を整えるボディケア / ボディシェイピングへと、領域を少しずつ広げていきます。<br><br>
      ひとつの商品を出すために、半年以上をかけて素材を選び、設計し、検品する。<br>
      量より質を、流行より定番を。KAPIBARAN のものづくりの軸はそこにあります。
    </p>
  </div>
</section>

<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow kbv2-text-center">
    <p class="kbv2-eyebrow">OPERATING COMPANY</p>
    <h2 class="kbv2-h2">運営会社</h2>
    <p class="kbv2-lead kbv2-mt-l">
      KAPIBARAN は <strong>SOLARWORKS 株式会社</strong> が運営するブランドです。<br>
      AI・ものづくり・D2C を軸に、暮らしを少し上にする事業を展開しています。<br>
      <span class="kbv3-about__email">お問い合わせ: {CONTACT_EMAIL}</span>
    </p>
  </div>
</section>

<section class="kbv2-cta">
  <div class="kbv2-container kbv2-text-center">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">PRODUCTS</p>
    <h2 class="kbv2-h2 kbv2-h2--light">わたしたちは、毎日のちいさな上質を、つくり続けます。</h2>
    <a href="/products/" class="kbv2-btn kbv2-btn--accent kbv2-mt-l">プロダクトを見る →</a>
  </div>
</section>
""")


# ===========================================================
# Products list page
# ===========================================================
def build_products(media_urls: Dict[str, str]):
    # Live products
    live_html = ""
    for p in PRODUCTS:
        main_img = _img_url(media_urls, p["main_image_key"])
        chips = "".join(
            f'<span class="kbv2-color-chip" style="background:{COLOR_HEX[c]};" title="{COLOR_JP[c]}"></span>'
            for c in p["colors"]
        )
        img_block = (
            f'<div class="kbv3-plist__img" style="background-image: url(\'{main_img}\');">'
            f'<span class="kbv2-plist__cat">{p["category_en"]}</span>'
            f'</div>'
        ) if main_img else ""
        live_html += f"""
      <a href="/products/{p['slug']}/" class="kbv2-plist__card kbv3-plist__card">
        {img_block}
        <div class="kbv2-plist__body">
          <p class="kbv2-plist__name">{p['full_name']}</p>
          <p class="kbv3-plist__msrp-label">{p['msrp_label']}</p>
          <p class="kbv2-plist__price">{p['price_display']}（税込）</p>
          <div class="kbv2-plist__colors">{chips}</div>
        </div>
      </a>"""

    # Coming Soon categories
    cs_html = ""
    for c in COMING_SOON_CATEGORIES:
        img_url = _img_url(media_urls, c["image_key"])
        img_block = (
            f'<div class="kbv3-cs-card__img" style="background-image: url(\'{img_url}\');">'
            f'<span class="kbv2-cs-card__badge">Coming Soon</span>'
            f'</div>'
        ) if img_url else f'<span class="kbv2-cs-card__badge">Coming Soon</span>'
        cs_html += f"""
      <div class="kbv2-cs-card kbv3-cs-card" id="{c['slug']}">
        {img_block}
        <p class="kbv2-cs-card__en">{c['category_en']}</p>
        <p class="kbv2-cs-card__jp">{c['category_jp']}</p>
        <p class="kbv2-cs-card__tagline">{c['tagline']}</p>
        <p class="kbv2-cs-card__schedule">{c['scheduled']}</p>
      </div>"""

    return html_block(f"""
<section class="kbv2-page-hero kbv2-page-hero--small">
  <div class="kbv2-page-hero__inner">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">PRODUCTS</p>
    <h1 class="kbv2-h1 kbv2-h1--light">プロダクト</h1>
    <p class="kbv2-page-hero__sub">暮らしを整える、上質な道具たち。</p>
  </div>
</section>

<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">NOW AVAILABLE</p>
      <h2 class="kbv2-h2">いまお届けしている商品</h2>
    </div>
    <div class="kbv2-plist__grid">{live_html}
    </div>
    <p class="kbv3-arrivals-note kbv2-text-center kbv2-mt-l">
      ※実際の販売価格・送料・在庫・保証等の詳細は、各販売店（Amazon・楽天市場・Yahoo!ショッピング）の商品ページをご確認ください。
    </p>
  </div>
</section>

<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">COMING SOON</p>
      <h2 class="kbv2-h2">次のカテゴリー</h2>
      <p class="kbv2-lead kbv2-text-center">体を整える領域へ、ブランドを広げていきます。</p>
    </div>
    <div class="kbv2-cs-grid">{cs_html}
    </div>
  </div>
</section>
""")


# ===========================================================
# Product detail page (per SKU)
# ===========================================================
def build_product_detail(product: dict, media_urls: Dict[str, str]):
    p = product
    main_img = _img_url(media_urls, p["main_image_key"])

    chips_html = ""
    for c in p["colors"]:
        c_img = _img_url(media_urls, p["color_image_keys"][c])
        chips_html += (
            f'<span class="kbv2-color-chip kbv2-color-chip--lg" '
            f'style="background:{COLOR_HEX[c]};" title="{COLOR_JP[c]}">'
            f'<span class="kbv2-color-chip__label">{COLOR_JP[c]}</span></span>'
        )

    # カラー別サムネ
    thumbs_html = ""
    for c in p["colors"]:
        c_img = _img_url(media_urls, p["color_image_keys"][c])
        if c_img:
            thumbs_html += (
                f'<div class="kbv3-pd__thumb" '
                f'style="background-image: url(\'{c_img}\');" '
                f'title="{COLOR_JP[c]}"></div>'
            )

    feats_html = ""
    for icon, t, sub in p["features"]:
        feats_html += f"""
      <div class="kbv2-feat">
        <div class="kbv2-feat__icon">{icon}</div>
        <p class="kbv2-feat__title">{t}</p>
        <p class="kbv2-feat__sub">{sub}</p>
      </div>"""

    spec_html = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in p["specs"])

    usage_html = ""
    for n, t, b in p["usage"]:
        usage_html += f"""
      <div class="kbv2-step">
        <div class="kbv2-step__no">{n}</div>
        <p class="kbv2-step__title">{t}</p>
        <p class="kbv2-step__body">{b}</p>
      </div>"""

    # EC ボタン — pending は disabled span として描画 (Codex #4: href="#" 残置による誤誘導禁止)
    ec_buttons = ""
    for label, key, cls in [
        ("Amazon で購入",        "amazon",  "kbv2-ec-btn--amazon"),
        ("楽天市場で購入",       "rakuten", "kbv2-ec-btn--rakuten"),
        ("Yahoo! ショッピングで購入", "yahoo",   "kbv2-ec-btn--yahoo"),
    ]:
        ec = p["ec_urls"].get(key, {"url": "#", "pending": True})
        if ec.get("pending") or not ec.get("url") or ec["url"] in ("", "#"):
            # 無効化: クリック不可 + ARIA で明示
            ec_buttons += (
                f'<span class="kbv2-ec-btn {cls} kbv3-cta--disabled" '
                f'aria-disabled="true" role="link" tabindex="-1" '
                f'data-todo="ec-url-pending">{label}（準備中）</span>\n        '
            )
        else:
            ec_buttons += (
                f'<a href="{ec["url"]}" class="kbv2-ec-btn {cls}" '
                f'rel="nofollow noopener" target="_blank">{label}</a>\n        '
            )

    # 商品ギャラリー
    gallery_html = (
        f'<div class="kbv3-pd__main-img" style="background-image: url(\'{main_img}\');"></div>'
        if main_img else
        f'<div class="kbv2-pd__art kbv2-pd__art--{p["slug"]}"><span class="kbv2-pd__sku">{p["sku"]}</span></div>'
    )
    if thumbs_html:
        gallery_html += f'<div class="kbv3-pd__thumbs">{thumbs_html}</div>'

    return html_block(f"""
<section class="kbv2-section kbv2-section--white kbv2-pd">
  <div class="kbv2-container">
    <p class="kbv2-breadcrumb"><a href="/">ホーム</a> ／ <a href="/products/">プロダクト</a> ／ {p['full_name']}</p>
    <div class="kbv2-pd__top">
      <div class="kbv2-pd__gallery">
        {gallery_html}
      </div>
      <div class="kbv2-pd__info">
        <p class="kbv2-pd__cat">{p['category_en']}</p>
        <h1 class="kbv2-h1">{p['full_name']}</h1>
        <p class="kbv2-pd__tagline">{p['tagline']}</p>

        <p class="kbv3-pd__msrp-label">{p['msrp_label']}</p>
        <p class="kbv2-pd__price">{p['price_display']}（税込）</p>
        <p class="kbv3-pd__msrp-note">{p['msrp_note']}</p>

        <p class="kbv3-pd__classification">機器分類: {p['jp_classification']}</p>

        <p class="kbv2-pd__color-label">カラー</p>
        <div class="kbv2-pd__colors">{chips_html}</div>

        <div class="kbv2-pd__ec-btns">
        {ec_buttons.rstrip()}
        </div>

        <div class="kbv3-pd__support-note">
          <p>● ご購入・配送・保証に関する詳細は、各販売店（Amazon・楽天市場・Yahoo!ショッピング）の規定に準じます。</p>
          <p>● 故障・修理・操作方法のご相談は、<a href="/contact/">KAPIBARAN サポート</a>（{CONTACT_EMAIL}）までお気軽にどうぞ。</p>
        </div>
      </div>
    </div>
  </div>
</section>

<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">PRODUCT FEATURES</p>
      <h2 class="kbv2-h2 kbv2-text-center">プロダクトの特長</h2>
    </div>
    <div class="kbv2-feats">{feats_html}
    </div>
  </div>
</section>

<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">SPECS</p>
      <h2 class="kbv2-h2 kbv2-text-center">スペック</h2>
    </div>
    <table class="kbv2-spec">{spec_html}</table>
  </div>
</section>

<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">HOW TO USE</p>
      <h2 class="kbv2-h2 kbv2-text-center">つかいかた</h2>
    </div>
    <div class="kbv2-steps">{usage_html}
    </div>
  </div>
</section>

<section class="kbv2-cta">
  <div class="kbv2-container kbv2-text-center">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">SUPPORT</p>
    <h2 class="kbv2-h2 kbv2-h2--light">ご質問・修理のご相談</h2>
    <p class="kbv2-cta__lead">どんな小さなご相談でも、お気軽にお寄せください。<br>
      <span class="kbv3-cta__email">{CONTACT_EMAIL}</span></p>
    <a href="/contact/" class="kbv2-btn kbv2-btn--accent">お問い合わせフォームへ</a>
  </div>
</section>
""")


# ===========================================================
# Contact / FAQ
# ===========================================================
def build_contact(media_urls: Dict[str, str]):
    faqs = [
        ("商品の保証期間はどのくらいですか？",
         "保証期間・保証内容は、お買い上げいただいた各販売店（Amazon・楽天市場・Yahoo!ショッピング）の規定に準じます。各商品ページをご確認ください。"),
        ("配送料はいくらですか？",
         "配送料は、お買い上げいただいた各販売店の規定に準じます。各商品ページをご確認ください。"),
        ("返品・交換は可能ですか？",
         "返品・交換のご対応は、お買い上げいただいた各販売店の規定に準じます。詳細は各商品ページをご確認のうえ、販売店のカスタマーサポートへお問い合わせください。"),
        ("カラーバリエーションはありますか？",
         "フットケア家電 KB-FC01 はネイビー / ベージュの 2 色、スマートトレッドミル KB-TM01 はオレンジ / ホワイト / ブルーの 3 色をご用意しています。"),
        ("各モール（Amazon・楽天・Yahoo!）で購入した商品も対応してもらえますか？",
         f"はい、製品自体の操作方法・修理に関するお問い合わせは KAPIBARAN サポート（{CONTACT_EMAIL}）にて承ります。お問い合わせフォームより「修理・サポート」を選択してご連絡ください。"),
        ("Coming Soon となっている商品はいつ発売されますか？",
         "ボディケア / ボディシェイピング領域は 2026 年内の段階的ローンチを予定しています。リリースの正式日程は本サイトと SNS でお知らせします。"),
        ("これらの商品は医療機器ですか？",
         "いいえ。KAPIBARAN の取り扱い製品は、リラクゼーション目的のリラクゼーション機器およびホームフィットネス機器であり、医療機器ではありません。"),
    ]
    faq_html = ""
    for q, a in faqs:
        faq_html += f"""
      <details class="kbv2-faq__item">
        <summary class="kbv2-faq__q">Q. {q}</summary>
        <p class="kbv2-faq__a">A. {a}</p>
      </details>"""

    return html_block(f"""
<section class="kbv2-page-hero kbv2-page-hero--small">
  <div class="kbv2-page-hero__inner">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">SUPPORT</p>
    <h1 class="kbv2-h1 kbv2-h1--light">お問い合わせ</h1>
    <p class="kbv2-page-hero__sub">どんな小さなご相談でも、お気軽にお寄せください。</p>
  </div>
</section>

<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container">
    <div class="kbv2-contact__cards">
      <div class="kbv2-contact__card">
        <div class="kbv2-contact__icon">?</div>
        <h3 class="kbv2-contact__title">よくあるご質問</h3>
        <p class="kbv2-contact__body">まずはこちらをご確認ください。</p>
      </div>
      <div class="kbv2-contact__card">
        <div class="kbv2-contact__icon">✉</div>
        <h3 class="kbv2-contact__title">メールでのお問い合わせ</h3>
        <p class="kbv2-contact__body">24 時間受付・原則 3 営業日以内にご返信します。<br>
          <strong>{CONTACT_EMAIL}</strong></p>
      </div>
      <div class="kbv2-contact__card">
        <div class="kbv2-contact__icon">⊕</div>
        <h3 class="kbv2-contact__title">修理・アフターサポート</h3>
        <p class="kbv2-contact__body">購入後のサポートはこちらから。</p>
      </div>
    </div>
  </div>
</section>

<section class="kbv2-section kbv2-section--bg">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">FAQ</p>
      <h2 class="kbv2-h2 kbv2-text-center">よくあるご質問</h2>
    </div>
    <div class="kbv2-faq">{faq_html}
    </div>
  </div>
</section>

<section id="form" class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow">
    <h2 class="kbv2-h2 kbv2-text-center">お問い合わせフォーム</h2>
    <div class="kbv2-mail-box">
      <p>送信フォームは Contact Form 7 設置後に有効化されます。</p>
      <p>お急ぎの方は、以下のメールアドレスまで直接ご連絡ください。</p>
      <p class="kbv2-mail-box__addr">{CONTACT_EMAIL}</p>
      <p class="kbv2-mail-box__meta">受付時間: 10:00–17:00（土日祝休）／ 対応言語: 日本語</p>
    </div>
  </div>
</section>
""")


# ===========================================================
# Legal pages (v2 から薬機/景表/メールアドレスのみ更新)
# ===========================================================
def build_tokushoho(media_urls: Dict[str, str] = None):
    rows = [
        ("販売事業者名",          "SOLARWORKS 株式会社"),
        ("運営責任者",            "（準備中）"),
        ("所在地",                "（準備中）"),
        ("お問い合わせ先",        CONTACT_EMAIL),
        ("販売価格",              "各商品ページにメーカー希望小売価格を記載。実販売価格は各販売店のページをご確認ください。"),
        ("販売価格以外の必要料金", "各販売店（Amazon・楽天市場・Yahoo!ショッピング）の規定に準じます。"),
        ("お支払い方法",          "各 EC モール所定の決済方法に準じます。"),
        ("引き渡し時期",          "各販売店の発送ポリシーに準じます。"),
        ("返品・交換",            "各販売店の返品・交換ポリシーに準じます。"),
        ("商品の販売形態",        "KAPIBARAN ブランドサイトからの直販は行っておりません。商品の販売は Amazon・楽天市場・Yahoo!ショッピング各店舗にて行います。"),
    ]
    rows_html = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)
    return html_block(f"""
<section class="kbv2-page-hero kbv2-page-hero--small">
  <div class="kbv2-page-hero__inner">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">LEGAL</p>
    <h1 class="kbv2-h1 kbv2-h1--light">特定商取引法に基づく表記</h1>
  </div>
</section>
<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow">
    <table class="kbv2-legal">{rows_html}</table>
    <p class="kbv2-text-center kbv2-sub kbv2-mt-l">※ 一部項目は準備中です。順次更新いたします。</p>
  </div>
</section>
""")


def build_privacy(media_urls: Dict[str, str] = None):
    sections = [
        ("第1条（個人情報の定義）",
         "本ポリシーにおいて「個人情報」とは、個人情報保護法第 2 条第 1 項に定める個人情報を意味します。"),
        ("第2条（個人情報の収集と利用目的）",
         "当社は、お問い合わせ対応・サービス向上のため、お名前・メールアドレス・お問い合わせ内容等を取得することがあります。"),
        ("第3条（第三者への提供）",
         "当社は、法令に基づく場合または利用者の同意がある場合を除き、個人情報を第三者に提供しません。"),
        ("第4条（Cookie および解析ツール）",
         "本サイトでは、利用状況の分析のため Google Analytics 4 を利用することがあります。Cookie の使用を拒否される場合は、ブラウザの設定で無効化できます。"),
        ("第5条（個人情報の開示・訂正・削除）",
         f"ご本人からの請求があった場合、合理的な範囲で対応いたします。{CONTACT_EMAIL} までご連絡ください。"),
        ("第6条（プライバシーポリシーの変更）",
         "本ポリシーの内容は、利用者への通知なく変更することがあります。"),
    ]
    body_html = ""
    for h, b in sections:
        body_html += f"<h3 class='kbv2-legal__h'>{h}</h3><p class='kbv2-legal__p'>{b}</p>"
    return html_block(f"""
<section class="kbv2-page-hero kbv2-page-hero--small">
  <div class="kbv2-page-hero__inner">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">LEGAL</p>
    <h1 class="kbv2-h1 kbv2-h1--light">プライバシーポリシー</h1>
  </div>
</section>
<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow">{body_html}
    <p class="kbv2-text-center kbv2-sub kbv2-mt-l">制定日: 2026 年 5 月 18 日</p>
  </div>
</section>
""")


def build_terms(media_urls: Dict[str, str] = None):
    sections = [
        ("第1条（適用）",
         "本規約は、SOLARWORKS 株式会社（以下「当社」）が運営する KAPIBARAN（本サイト）のすべての利用者に適用されます。"),
        ("第2条（利用登録）",
         "本サイトの利用にあたって、利用者は本規約に同意したものとみなされます。"),
        ("第3条（禁止事項）",
         "利用者は、法令違反、第三者の権利侵害、当社サービスの妨害行為等を行ってはなりません。"),
        ("第4条（外部 EC サイトでの購入）",
         "Amazon・楽天市場・Yahoo! ショッピング等の外部サイトでの購入については、各プラットフォームの利用規約および販売店規定が適用されます。"),
        ("第5条（免責）",
         "当社は、本サイトの掲載情報の正確性に努めますが、その完全性を保証するものではありません。"),
        ("第6条（規約の変更）",
         "本規約は、当社の判断により利用者への事前通知なく変更されることがあります。"),
        ("第7条（準拠法・管轄）",
         "本規約の解釈は日本法に準拠し、本規約に関する紛争は当社所在地を管轄する裁判所を専属管轄とします。"),
    ]
    body_html = ""
    for h, b in sections:
        body_html += f"<h3 class='kbv2-legal__h'>{h}</h3><p class='kbv2-legal__p'>{b}</p>"
    return html_block(f"""
<section class="kbv2-page-hero kbv2-page-hero--small">
  <div class="kbv2-page-hero__inner">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">LEGAL</p>
    <h1 class="kbv2-h1 kbv2-h1--light">利用規約</h1>
  </div>
</section>
<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container kbv2-narrow">{body_html}
    <p class="kbv2-text-center kbv2-sub kbv2-mt-l">制定日: 2026 年 5 月 18 日</p>
  </div>
</section>
""")
