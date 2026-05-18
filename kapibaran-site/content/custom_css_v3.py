# -*- coding: utf-8 -*-
"""KAPIBARAN v3 — v2 CSS + v3 追加 (画像対応 / MSRP表記 / support note)

v2 CSS は SWELL Additional CSS にすでに投入済。
v3 は append style で上書き/拡張する。
"""

from custom_css_v2 import CSS_V2 as _CSS_V2


CSS_V3_EXTRA = """
/* ==========================================================
   KAPIBARAN v3 — 画像投入 / MSRP / Compliance UI 追加
========================================================== */

/* ---- Hero with image background ---- */
.kbv3-hero {
  background-size: cover !important;
  background-position: center center !important;
  background-repeat: no-repeat !important;
  position: relative;
  isolation: isolate;
}
.kbv3-hero .kbv2-hero__inner { position: relative; z-index: 2; }
.kbv3-hero__overlay {
  position: absolute;
  inset: 0;
  background: rgba(43, 34, 24, 0.35);
  z-index: 1;
  pointer-events: none;
}
.kbv3-hero::before { content: none !important; }

/* ---- Page hero with image ---- */
.kbv3-page-hero {
  background-size: cover !important;
  background-position: center center !important;
  background-repeat: no-repeat !important;
  position: relative;
  isolation: isolate;
}
.kbv3-page-hero .kbv2-page-hero__inner { position: relative; z-index: 2; }
.kbv3-page-hero .kbv3-hero__overlay {
  position: absolute; inset: 0;
  background: rgba(43, 34, 24, 0.35);
  z-index: 1; pointer-events: none;
}

/* ---- Category card with photo ---- */
.kbv3-cat-card__img {
  position: relative;
  aspect-ratio: 4 / 3;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  border-bottom: 1px solid var(--kb-line);
}

/* ---- Product card with photo ---- */
.kbv3-prod-card__img {
  aspect-ratio: 4 / 3;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  border-bottom: 1px solid var(--kb-line);
}

/* ---- Product list (Products page) with photo ---- */
.kbv3-plist__img {
  position: relative;
  aspect-ratio: 4 / 3;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  border-bottom: 1px solid var(--kb-line);
}

/* ---- Coming Soon card with photo ---- */
.kbv3-cs-card__img {
  position: relative;
  aspect-ratio: 4 / 3;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  margin: -2.4rem -1.6rem 1.4rem;
  border-radius: 6px 6px 0 0;
}
.kbv3-cs-card__img .kbv2-cs-card__badge {
  top: 12px; right: 12px;
}

/* ---- MSRP labels (Top NEW ARRIVALS / Products list) ---- */
.kbv3-prod-card__msrp-label,
.kbv3-plist__msrp-label,
.kbv3-pd__msrp-label {
  font-family: var(--kb-font-en); font-style: italic;
  font-size: 0.75rem; letter-spacing: 0.16em;
  color: #6B5D4F; margin: 0 0 0.25rem;
}

/* ---- Arrivals note ---- */
.kbv3-arrivals-note {
  font-family: var(--kb-font-sans);
  font-size: 0.78rem; color: var(--kb-text-sub);
  line-height: 1.85; max-width: 720px; margin: 1.2rem auto 0;
}

/* ---- Product detail gallery (with photo) ---- */
.kbv3-pd__main-img {
  aspect-ratio: 1 / 1;
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  border-radius: 6px;
  border: 1px solid var(--kb-line);
}
.kbv3-pd__thumbs {
  display: flex; gap: 0.6rem; margin-top: 0.8rem; flex-wrap: wrap;
}
.kbv3-pd__thumb {
  width: 84px; height: 84px;
  background-size: cover; background-position: center; background-repeat: no-repeat;
  border-radius: 4px; border: 1px solid var(--kb-line);
  cursor: pointer; transition: transform 0.2s ease, border-color 0.2s ease;
}
.kbv3-pd__thumb:hover { transform: translateY(-2px); border-color: var(--kb-amber); }

/* ---- Product detail MSRP / classification / support-note ---- */
.kbv3-pd__msrp-note {
  font-family: var(--kb-font-sans);
  font-size: 0.78rem; color: var(--kb-text-sub);
  line-height: 1.85;
  border-top: 1px solid var(--kb-line);
  padding-top: 0.9rem;
  margin: 0.5rem 0 1.6rem;
}
.kbv3-pd__classification {
  display: inline-block;
  background: var(--kb-cream);
  font-family: var(--kb-font-sans);
  font-size: 0.78rem;
  color: var(--kb-text);
  padding: 0.45rem 0.9rem;
  border-radius: 999px;
  margin: 0 0 1.4rem;
  letter-spacing: 0.04em;
}
.kbv3-pd__support-note {
  margin: 1.2rem 0 0;
  padding: 1.1rem 1.3rem;
  background: var(--kb-offwhite);
  border-radius: 6px;
  font-family: var(--kb-font-sans);
  font-size: 0.85rem;
  color: var(--kb-text-sub);
  line-height: 1.85;
  border-left: 3px solid var(--kb-amber);
}
.kbv3-pd__support-note p { margin: 0 0 0.4rem; }
.kbv3-pd__support-note p:last-child { margin: 0; }
.kbv3-pd__support-note a { color: var(--kb-navy); text-decoration: underline; }

/* ---- v2 の kbv2-pd__benefits を完全非表示 (法令違反バッジ撤去) ---- */
.kbv2-pd__benefits { display: none !important; }

/* ---- CTA email ---- */
.kbv3-cta__email,
.kbv3-about__email {
  display: inline-block;
  font-family: var(--kb-font-en-sans);
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--kb-amber-soft);
  margin-top: 0.4rem;
}
.kbv3-about__email {
  display: block; margin-top: 0.8rem; color: var(--kb-amber);
}

/* ---- Hide old gradient art if img class is present ---- */
.kbv3-cat-card .kbv2-cat-card__art { display: none !important; }
.kbv3-prod-card .kbv2-prod-card__art { display: none !important; }
.kbv3-plist__card .kbv2-plist__art { display: none !important; }
"""


CSS_V3 = _CSS_V2 + "\n\n" + CSS_V3_EXTRA
