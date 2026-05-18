# -*- coding: utf-8 -*-
"""KAPIBARAN v2 ページビルダー (CEO 確定 5SKU + Coming Soon 対応)

色: Deep Navy #1F2A44 + Sunset Amber #C96E12 + Off White #F8F6F2
   ※ CEO 5/13 ブランドカラー指示に整合

全ページが kbv2- 名前空間 CSS で完結する。
SWELL の素 CSS には依存しないため、子テーマ無しでも視覚一貫性が保てる。
"""
from __future__ import annotations
from products_v2 import PRODUCTS, COMING_SOON_CATEGORIES, COLOR_HEX, COLOR_JP, get_all_categories


def html_block(inner: str) -> str:
    return f"<!-- wp:html -->\n{inner.strip()}\n<!-- /wp:html -->"


# ===========================================================
# TOP page
# ===========================================================
def build_top():
    parts = []

    # ---- Hero ----
    parts.append(html_block("""
<section class="kbv2-hero">
  <div class="kbv2-hero__inner">
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

    # ---- CATEGORIES (4 cards, 2 available + 2 Coming Soon) ----
    cats = get_all_categories()
    cards_html = ""
    for c in cats:
        badge = '<span class="kbv2-cat-card__badge">Coming Soon</span>' if c["status"] == "coming_soon" else ""
        muted = "kbv2-cat-card--muted" if c["status"] == "coming_soon" else ""
        cards_html += f"""
    <a href="{c['link']}" class="kbv2-cat-card {muted}">
      <div class="kbv2-cat-card__art kbv2-cat-card__art--{c['en'].lower().replace(' ', '-')}">{badge}</div>
      <p class="kbv2-cat-card__en">{c['en']}</p>
      <p class="kbv2-cat-card__jp">{c['jp']}</p>
      <p class="kbv2-cat-card__sub">{c['subtitle']}</p>
    </a>"""
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

    # ---- NEW ARRIVALS (Real 2 products) ----
    arr_html = ""
    for p in PRODUCTS:
        chips = "".join(
            f'<span class="kbv2-color-chip" style="background:{COLOR_HEX[c]};" title="{COLOR_JP[c]}"></span>'
            for c in p["colors"]
        )
        arr_html += f"""
    <a href="/products/{p['slug']}/" class="kbv2-prod-card">
      <div class="kbv2-prod-card__art kbv2-prod-card__art--{p['slug']}">
        <span class="kbv2-prod-card__sku">{p['sku']}</span>
      </div>
      <div class="kbv2-prod-card__body">
        <p class="kbv2-prod-card__cat">{p['category_en']}</p>
        <p class="kbv2-prod-card__name">{p['full_name']}</p>
        <p class="kbv2-prod-card__price">{p['price_display']} <span class="kbv2-prod-card__tax">税込</span></p>
        <div class="kbv2-prod-card__colors">{chips}</div>
      </div>
    </a>"""
    parts.append(html_block(f"""
<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">PRODUCTS</p>
      <h2 class="kbv2-h2-en">Products</h2>
      <p class="kbv2-lead kbv2-text-center">新しく、暮らしへ。</p>
    </div>
    <div class="kbv2-prod-grid">{arr_html}
    </div>
    <p class="kbv2-text-center kbv2-mt-xl">
      <a href="/products/" class="kbv2-btn kbv2-btn--ghost">すべての商品を見る →</a>
    </p>
  </div>
</section>
"""))

    # ---- BRAND VALUES ----
    parts.append(html_block("""
<section class="kbv2-section kbv2-section--bg">
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
    parts.append(html_block("""
<section class="kbv2-cta">
  <div class="kbv2-container kbv2-text-center">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">SUPPORT</p>
    <h2 class="kbv2-h2 kbv2-h2--light">お問い合わせ・修理のご依頼</h2>
    <p class="kbv2-cta__lead">プロダクトに関するご質問、サポート、修理のご依頼はこちらから。</p>
    <a href="/contact/" class="kbv2-btn kbv2-btn--accent">お問い合わせフォームへ</a>
  </div>
</section>
"""))

    return "\n\n".join(parts)


# ===========================================================
# About page
# ===========================================================
def build_about():
    return html_block("""
<section class="kbv2-page-hero">
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
      AI・ものづくり・D2C を軸に、暮らしを少し上にする事業を展開しています。
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
def build_products():
    # Live products
    live_html = ""
    for p in PRODUCTS:
        chips = "".join(
            f'<span class="kbv2-color-chip" style="background:{COLOR_HEX[c]};" title="{COLOR_JP[c]}"></span>'
            for c in p["colors"]
        )
        live_html += f"""
      <a href="/products/{p['slug']}/" class="kbv2-plist__card">
        <div class="kbv2-plist__art kbv2-plist__art--{p['slug']}">
          <span class="kbv2-plist__cat">{p['category_en']}</span>
          <span class="kbv2-plist__sku">{p['sku']}</span>
        </div>
        <div class="kbv2-plist__body">
          <p class="kbv2-plist__name">{p['full_name']}</p>
          <p class="kbv2-plist__price">{p['price_display']} <span class="kbv2-plist__tax">税込</span></p>
          <div class="kbv2-plist__colors">{chips}</div>
        </div>
      </a>"""

    # Coming soon categories — explicit blocks
    cs_html = ""
    for c in COMING_SOON_CATEGORIES:
        cs_html += f"""
      <div class="kbv2-cs-card" id="{c['slug']}">
        <span class="kbv2-cs-card__badge">Coming Soon</span>
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
def build_product_detail(product: dict):
    p = product

    chips_html = "".join(
        f'<span class="kbv2-color-chip kbv2-color-chip--lg" style="background:{COLOR_HEX[c]};" title="{COLOR_JP[c]}">'
        f'<span class="kbv2-color-chip__label">{COLOR_JP[c]}</span></span>'
        for c in p["colors"]
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

    reviews_html = ""
    for stars, body in p["reviews"]:
        reviews_html += f"""
      <div class="kbv2-review">
        <p class="kbv2-review__stars">{stars}</p>
        <p class="kbv2-review__body">「{body}」</p>
      </div>"""

    return html_block(f"""
<section class="kbv2-section kbv2-section--white kbv2-pd">
  <div class="kbv2-container">
    <p class="kbv2-breadcrumb"><a href="/">ホーム</a> ／ <a href="/products/">プロダクト</a> ／ {p['full_name']}</p>
    <div class="kbv2-pd__top">
      <div class="kbv2-pd__gallery">
        <div class="kbv2-pd__art kbv2-pd__art--{p['slug']}">
          <span class="kbv2-pd__sku">{p['sku']}</span>
        </div>
      </div>
      <div class="kbv2-pd__info">
        <p class="kbv2-pd__cat">{p['category_en']}</p>
        <h1 class="kbv2-h1">{p['full_name']}</h1>
        <p class="kbv2-pd__tagline">{p['tagline']}</p>
        <p class="kbv2-pd__price">{p['price_display']} <span class="kbv2-pd__tax">税込・送料無料</span></p>

        <p class="kbv2-pd__color-label">カラー</p>
        <div class="kbv2-pd__colors">{chips_html}</div>

        <div class="kbv2-pd__ec-btns">
          <a href="#" class="kbv2-ec-btn kbv2-ec-btn--amazon" rel="nofollow noopener" target="_blank">Amazon で購入</a>
          <a href="#" class="kbv2-ec-btn kbv2-ec-btn--rakuten" rel="nofollow noopener" target="_blank">楽天市場で購入</a>
          <a href="#" class="kbv2-ec-btn kbv2-ec-btn--yahoo" rel="nofollow noopener" target="_blank">Yahoo! ショッピングで購入</a>
        </div>

        <!-- v3.1 (Codex #1 / 2026-05-18): 確定表記の benefits リストは markup ごと削除 -->
        <!-- 旧: <ul class="kbv2-pd__benefits"> 全国 送料無料 / 1 年メーカー保証 / 国内サポート対応 </ul> -->
        <!-- 代替: 商品詳細ページ側で kbv3-pd__support-note (販売店規定に準じる旨) を表示 -->
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

<section class="kbv2-section kbv2-section--white">
  <div class="kbv2-container">
    <div class="kbv2-section__head">
      <p class="kbv2-eyebrow">CUSTOMER VOICE</p>
      <h2 class="kbv2-h2 kbv2-text-center">お客様の声</h2>
    </div>
    <div class="kbv2-reviews">{reviews_html}
    </div>
  </div>
</section>

<section class="kbv2-cta">
  <div class="kbv2-container kbv2-text-center">
    <p class="kbv2-eyebrow kbv2-eyebrow--light">SUPPORT</p>
    <h2 class="kbv2-h2 kbv2-h2--light">ご質問・修理のご相談</h2>
    <p class="kbv2-cta__lead">どんな小さなご相談でも、お気軽にお寄せください。</p>
    <a href="/contact/" class="kbv2-btn kbv2-btn--accent">お問い合わせフォームへ</a>
  </div>
</section>
""")


# ===========================================================
# Contact / FAQ
# ===========================================================
def build_contact():
    faqs = [
        ("商品の保証期間はどのくらいですか？",
         "KAPIBARAN の全商品は、ご購入から 1 年間のメーカー保証が付帯されています。通常使用範囲での不具合の場合、無償にて修理または交換のご対応をいたします。"),
        ("配送料はかかりますか？",
         "本サイト掲載のすべての商品は、全国送料無料でお届けしております。"),
        ("返品・交換は可能ですか？",
         "未開封・未使用のものに限り、商品到着後 7 日以内であれば返品交換が可能です。詳細はお問い合わせください。"),
        ("カラーバリエーションはありますか？",
         "フットケア家電 KB-FC01 はネイビー / ベージュの 2 色、スマートトレッドミル KB-TM01 はオレンジ / ホワイト / ブルーの 3 色をご用意しています。"),
        ("各モール（Amazon・楽天・Yahoo!）で購入した商品も対応してもらえますか？",
         "はい、ご対応いたします。お問い合わせフォームより「修理・サポート」を選択してご連絡ください。"),
        ("Coming Soon となっている商品はいつ発売されますか？",
         "ボディケア / ボディシェイピング領域は 2026 年内の段階的ローンチを予定しています。リリースの正式日程は本サイトと SNS でお知らせします。"),
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
        <p class="kbv2-contact__body">24 時間受付・原則 3 営業日以内にご返信します。</p>
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
      <p class="kbv2-mail-box__addr">support@kapibaran.com</p>
      <p class="kbv2-mail-box__meta">受付時間: 10:00–17:00（土日祝休）／ 対応言語: 日本語</p>
    </div>
  </div>
</section>
""")


# ===========================================================
# Legal pages
# ===========================================================
def build_tokushoho():
    rows = [
        ("販売事業者名",          "SOLARWORKS 株式会社"),
        ("運営責任者",            "（準備中）"),
        ("所在地",                "（準備中）"),
        ("お問い合わせ先",        "support@kapibaran.com"),
        ("販売価格",              "各商品ページに記載（税込・送料込）"),
        ("販売価格以外の必要料金", "代引き手数料・銀行振込手数料（必要に応じて）"),
        ("お支払い方法",          "クレジットカード／銀行振込／代金引換／各 EC モール所定の決済方法"),
        ("引き渡し時期",          "ご入金確認後 3 営業日以内に発送（在庫状況により異なります）"),
        ("返品・交換",            "商品到着後 7 日以内・未開封品に限り返品交換可"),
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


def build_privacy():
    sections = [
        ("第1条（個人情報の定義）",
         "本ポリシーにおいて「個人情報」とは、個人情報保護法第 2 条第 1 項に定める個人情報を意味します。"),
        ("第2条（個人情報の収集と利用目的）",
         "当社は、商品のご注文・お問い合わせ対応・サービス向上のため、お名前・メールアドレス・電話番号・ご住所・お支払い情報等を取得することがあります。"),
        ("第3条（第三者への提供）",
         "当社は、法令に基づく場合または利用者の同意がある場合を除き、個人情報を第三者に提供しません。"),
        ("第4条（Cookie および解析ツール）",
         "本サイトでは、利用状況の分析のため Google Analytics 4 を利用することがあります。Cookie の使用を拒否される場合は、ブラウザの設定で無効化できます。"),
        ("第5条（個人情報の開示・訂正・削除）",
         "ご本人からの請求があった場合、合理的な範囲で対応いたします。お問い合わせフォームよりご連絡ください。"),
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


def build_terms():
    sections = [
        ("第1条（適用）",
         "本規約は、SOLARWORKS 株式会社（以下「当社」）が運営する KAPIBARAN（本サイト）のすべての利用者に適用されます。"),
        ("第2条（利用登録）",
         "本サイトの利用にあたって、利用者は本規約に同意したものとみなされます。"),
        ("第3条（禁止事項）",
         "利用者は、法令違反、第三者の権利侵害、当社サービスの妨害行為等を行ってはなりません。"),
        ("第4条（外部 EC サイトでの購入）",
         "Amazon・楽天市場・Yahoo! ショッピング等の外部サイトでの購入については、各プラットフォームの利用規約が適用されます。"),
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
