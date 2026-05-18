# -*- coding: utf-8 -*-
"""KAPIBARAN v2 サイト全体 Custom CSS

Deep Navy + Sunset Amber + Off White の三色構成。
全コンポーネントは kbv2- 名前空間で完結し、SWELL 既定スタイルに依存しない。
SWELL の Additional CSS 領域に投入する。
"""

CSS_V2 = """
/* ==========================================================
   KAPIBARAN v2 — Brand Design System
   Palette: Deep Navy #1F2A44 / Sunset Amber #C96E12 / Off White #F8F6F2
========================================================== */
:root {
  --kb-navy:        #1F2A44;
  --kb-navy-deep:   #15192E;
  --kb-navy-soft:   #2A3654;
  --kb-amber:       #C96E12;
  --kb-amber-soft:  #E08A37;
  --kb-amber-dim:   #A95A0E;
  --kb-offwhite:    #F8F6F2;
  --kb-cream:       #EFEAE0;
  --kb-text:        #1F2A44;
  --kb-text-sub:    #5A6480;
  --kb-line:        #E4DED1;

  --kb-font-jp:     "Noto Serif JP", "Yu Mincho", "游明朝", serif;
  --kb-font-sans:   "Noto Sans JP", "Hiragino Sans", "Yu Gothic", sans-serif;
  --kb-font-en:     "Cormorant Garamond", "Times New Roman", serif;
  --kb-font-en-sans:"Inter", "Helvetica Neue", sans-serif;
}

/* SWELL の既存装飾を一掃（main コンテンツ内に限定） */
.post_content h2.kbv2-h2::before,
.post_content h2.kbv2-h2::after,
.post_content h2.kbv2-h2-en::before,
.post_content h2.kbv2-h2-en::after { content: none !important; display: none !important; }
.post_content h2.kbv2-h2,
.post_content h2.kbv2-h2-en {
  background: transparent !important; padding: 0 !important;
  border: 0 !important; box-shadow: none !important;
}

/* ---- 基本タイポ ---- */
.kbv2-eyebrow {
  font-family: var(--kb-font-en-sans);
  letter-spacing: 0.18em; font-size: 0.78rem;
  color: var(--kb-amber); margin: 0 0 0.6rem; text-transform: uppercase;
}
.kbv2-eyebrow--light { color: var(--kb-amber-soft); }

.kbv2-h1, .post_content .kbv2-h1 {
  font-family: var(--kb-font-jp); font-weight: 600;
  font-size: clamp(1.6rem, 3.4vw, 2.4rem); line-height: 1.45;
  color: var(--kb-text); margin: 0 0 1rem;
}
.kbv2-h1--light, .post_content .kbv2-h1--light { color: #FFFFFF !important; }

.kbv2-h2, .post_content .kbv2-h2 {
  font-family: var(--kb-font-jp); font-weight: 600;
  font-size: clamp(1.4rem, 2.6vw, 1.95rem); line-height: 1.5;
  color: var(--kb-text); margin: 0 0 1rem;
}
.kbv2-h2--light, .post_content .kbv2-h2--light { color: #FFFFFF !important; }
.kbv2-h2-en, .post_content .kbv2-h2-en {
  font-family: var(--kb-font-en); font-weight: 500;
  font-size: clamp(1.8rem, 3.6vw, 2.6rem); letter-spacing: 0.04em;
  color: var(--kb-text); margin: 0 0 0.6rem; text-align: center;
}

.kbv2-lead {
  font-family: var(--kb-font-sans); font-size: 1.02rem; line-height: 1.95;
  color: var(--kb-text); margin: 0 0 1rem;
}
.kbv2-sub  { color: var(--kb-text-sub); font-size: 0.86rem; }
.kbv2-text-center { text-align: center !important; }
.kbv2-mt-l  { margin-top: 1.6rem !important; }
.kbv2-mt-xl { margin-top: 2.6rem !important; }

/* ---- レイアウト ---- */
.kbv2-container { max-width: 1180px; margin: 0 auto; padding: 0 1.4rem; }
.kbv2-narrow    { max-width: 760px; }
.kbv2-section   { padding: clamp(3rem, 7vw, 5.4rem) 0; }
.kbv2-section--white { background: #FFFFFF; }
.kbv2-section--bg    { background: var(--kb-offwhite); }
.kbv2-section__head  { text-align: center; margin-bottom: clamp(2rem, 4vw, 3rem); }

/* ---- ボタン ---- */
.kbv2-btn {
  display: inline-block; padding: 0.95rem 2.2rem; border-radius: 999px;
  font-family: var(--kb-font-sans); font-size: 0.96rem; font-weight: 500;
  letter-spacing: 0.04em; text-decoration: none; transition: all 0.25s ease;
  border: 1.5px solid transparent; cursor: pointer;
}
.kbv2-btn--primary { background: var(--kb-navy); color: #FFFFFF; border-color: var(--kb-navy); }
.kbv2-btn--primary:hover { background: var(--kb-navy-deep); border-color: var(--kb-navy-deep); }
.kbv2-btn--accent  { background: var(--kb-amber); color: #FFFFFF; border-color: var(--kb-amber); }
.kbv2-btn--accent:hover { background: var(--kb-amber-dim); border-color: var(--kb-amber-dim); }
.kbv2-btn--ghost   { background: transparent; color: var(--kb-navy); border-color: var(--kb-navy); }
.kbv2-btn--ghost:hover { background: var(--kb-navy); color: #FFFFFF; }

/* ---- Hero ---- */
.kbv2-hero {
  background: linear-gradient(135deg, var(--kb-navy) 0%, var(--kb-navy-deep) 100%);
  color: #FFFFFF; padding: clamp(5rem, 10vw, 8rem) 0;
  position: relative; overflow: hidden;
}
.kbv2-hero::before {
  content: ""; position: absolute; top: -120px; right: -80px;
  width: 360px; height: 360px; border-radius: 50%;
  background: radial-gradient(circle at center, rgba(201,110,18,0.30) 0%, transparent 70%);
  pointer-events: none;
}
.kbv2-hero__inner {
  max-width: 1180px; margin: 0 auto; padding: 0 1.4rem; position: relative;
  text-align: center;
}
.kbv2-hero__en {
  font-family: var(--kb-font-en); font-style: italic; font-size: clamp(1.4rem, 3vw, 2.1rem);
  letter-spacing: 0.05em; color: var(--kb-amber-soft); margin: 0 0 0.4rem;
}
.kbv2-hero__jp {
  font-family: var(--kb-font-jp); font-weight: 600;
  font-size: clamp(1.9rem, 4.4vw, 3.1rem); line-height: 1.4;
  color: #FFFFFF; margin: 0 0 1.6rem;
}
.kbv2-hero__lead {
  font-family: var(--kb-font-sans); font-size: 1rem; line-height: 1.95;
  color: rgba(255,255,255,0.86); margin: 0 auto 2.4rem; max-width: 620px;
}

/* ---- Page Hero (sub pages) ---- */
.kbv2-page-hero {
  background: linear-gradient(135deg, var(--kb-navy) 0%, var(--kb-navy-soft) 100%);
  color: #FFFFFF; padding: clamp(4rem, 8vw, 6rem) 0; text-align: center;
}
.kbv2-page-hero--small { padding: clamp(3rem, 6vw, 4.5rem) 0; }
.kbv2-page-hero__inner { max-width: 1180px; margin: 0 auto; padding: 0 1.4rem; }
.kbv2-page-hero__sub {
  font-family: var(--kb-font-sans); color: rgba(255,255,255,0.82);
  margin: 0.6rem 0 0; font-size: 0.96rem;
}

/* ---- Category cards (Top) ---- */
.kbv2-cat-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 1.6rem; margin-top: 1rem;
}
@media (max-width: 900px) { .kbv2-cat-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 520px) { .kbv2-cat-grid { grid-template-columns: 1fr; } }

.kbv2-cat-card {
  display: block; background: #FFFFFF; border-radius: 6px;
  text-decoration: none; color: var(--kb-text);
  border: 1px solid var(--kb-line);
  transition: transform 0.25s ease, box-shadow 0.25s ease;
  overflow: hidden;
}
.kbv2-cat-card:hover { transform: translateY(-4px); box-shadow: 0 12px 28px rgba(31,42,68,0.10); }
.kbv2-cat-card--muted { opacity: 0.78; }
.kbv2-cat-card__art {
  position: relative; aspect-ratio: 4 / 3;
  display: flex; align-items: center; justify-content: center;
}
.kbv2-cat-card__art--foot-care    { background: linear-gradient(135deg, #E8DDC8 0%, #D8C8A8 100%); }
.kbv2-cat-card__art--home-fitness { background: linear-gradient(135deg, #E08A37 0%, #C96E12 100%); }
.kbv2-cat-card__art--body-care    { background: linear-gradient(135deg, #D9D2C4 0%, #B9B1A0 100%); }
.kbv2-cat-card__art--body-shaping { background: linear-gradient(135deg, #5A6E94 0%, #3D5278 100%); }
.kbv2-cat-card__badge {
  position: absolute; top: 12px; right: 12px;
  background: rgba(31,42,68,0.86); color: #FFFFFF;
  font-family: var(--kb-font-en-sans); font-size: 0.7rem; letter-spacing: 0.14em;
  padding: 0.35rem 0.8rem; border-radius: 999px;
}
.kbv2-cat-card__en {
  font-family: var(--kb-font-en-sans); letter-spacing: 0.16em;
  font-size: 0.74rem; color: var(--kb-amber);
  margin: 1.2rem 1.2rem 0.2rem;
}
.kbv2-cat-card__jp {
  font-family: var(--kb-font-jp); font-weight: 600; font-size: 1.1rem;
  color: var(--kb-text); margin: 0 1.2rem 0.4rem;
}
.kbv2-cat-card__sub {
  font-family: var(--kb-font-sans); font-size: 0.86rem;
  color: var(--kb-text-sub); margin: 0 1.2rem 1.2rem;
}

/* ---- Product cards (Top) ---- */
.kbv2-prod-grid {
  display: grid; grid-template-columns: repeat(2, 1fr);
  gap: 2rem; margin-top: 1rem;
}
@media (max-width: 700px) { .kbv2-prod-grid { grid-template-columns: 1fr; } }
.kbv2-prod-card {
  display: block; background: #FFFFFF; border-radius: 6px;
  text-decoration: none; color: var(--kb-text); overflow: hidden;
  border: 1px solid var(--kb-line);
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.kbv2-prod-card:hover { transform: translateY(-4px); box-shadow: 0 16px 32px rgba(31,42,68,0.10); }
.kbv2-prod-card__art {
  position: relative; aspect-ratio: 4 / 3;
  display: flex; align-items: center; justify-content: center;
}
.kbv2-prod-card__art--footcare-kb-fc01 {
  background: linear-gradient(135deg, #2A3654 0%, #1F2A44 60%, #15192E 100%);
}
.kbv2-prod-card__art--treadmill-kb-tm01 {
  background: linear-gradient(135deg, #E08A37 0%, #C96E12 100%);
}
.kbv2-prod-card__sku, .kbv2-plist__sku, .kbv2-pd__sku {
  font-family: var(--kb-font-en-sans); letter-spacing: 0.18em;
  font-size: 0.96rem; color: #FFFFFF;
  background: rgba(0,0,0,0.32); padding: 0.5rem 1rem; border-radius: 4px;
}
.kbv2-prod-card__body { padding: 1.4rem 1.4rem 1.6rem; }
.kbv2-prod-card__cat {
  font-family: var(--kb-font-en-sans); letter-spacing: 0.14em;
  font-size: 0.72rem; color: var(--kb-amber); margin: 0 0 0.3rem;
}
.kbv2-prod-card__name {
  font-family: var(--kb-font-jp); font-weight: 600;
  font-size: 1.1rem; color: var(--kb-text); margin: 0 0 0.6rem;
}
.kbv2-prod-card__price {
  font-family: var(--kb-font-en-sans); font-weight: 600; font-size: 1.3rem;
  color: var(--kb-text); margin: 0 0 0.8rem;
}
.kbv2-prod-card__tax {
  font-family: var(--kb-font-sans); font-weight: 400;
  font-size: 0.74rem; color: var(--kb-text-sub); margin-left: 0.5rem;
}
.kbv2-prod-card__colors { display: flex; gap: 0.4rem; }

/* ---- Color chips ---- */
.kbv2-color-chip {
  display: inline-block; width: 22px; height: 22px; border-radius: 50%;
  border: 1.5px solid #FFFFFF; box-shadow: 0 0 0 1px var(--kb-line);
}
.kbv2-color-chip--lg {
  width: auto; height: auto; border-radius: 999px; padding: 0.45rem 0.9rem 0.45rem 1.8rem;
  position: relative; background-clip: border-box; color: var(--kb-text);
  box-shadow: 0 0 0 1px var(--kb-line);
}
.kbv2-color-chip--lg::before {
  content: ""; position: absolute; left: 0.55rem; top: 50%; transform: translateY(-50%);
  width: 16px; height: 16px; border-radius: 50%; background: inherit;
  border: 1px solid rgba(0,0,0,0.18);
}
.kbv2-color-chip__label {
  font-family: var(--kb-font-sans); font-size: 0.82rem;
  background: #FFFFFF; padding: 0 0.4rem; border-radius: 4px;
  position: relative; z-index: 1;
}

/* ---- Brand values ---- */
.kbv2-values {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 1.6rem; margin-top: 1rem;
}
@media (max-width: 800px) { .kbv2-values { grid-template-columns: 1fr; } }
.kbv2-value {
  background: #FFFFFF; padding: 2rem 1.6rem; border-radius: 6px;
  text-align: center; border: 1px solid var(--kb-line);
}
.kbv2-value__icon {
  font-family: var(--kb-font-en); font-size: 1.6rem; color: var(--kb-amber);
  margin-bottom: 0.6rem;
}
.kbv2-value__title {
  font-family: var(--kb-font-jp); font-weight: 600; font-size: 1.1rem;
  color: var(--kb-text); margin: 0 0 0.8rem;
}
.kbv2-value__body {
  font-family: var(--kb-font-sans); font-size: 0.9rem; line-height: 1.85;
  color: var(--kb-text-sub); margin: 0;
}

/* ---- CTA section ---- */
.kbv2-cta {
  background: linear-gradient(135deg, var(--kb-navy) 0%, var(--kb-navy-deep) 100%);
  color: #FFFFFF; padding: clamp(3rem, 6vw, 4.5rem) 0;
}
.kbv2-cta__lead {
  color: rgba(255,255,255,0.86); font-family: var(--kb-font-sans);
  font-size: 0.96rem; margin: 0.8rem auto 1.6rem; max-width: 560px;
}

/* ---- Timeline ---- */
.kbv2-timeline {
  position: relative; padding-left: 1.6rem; border-left: 2px solid var(--kb-amber);
}
.kbv2-timeline__item {
  display: flex; gap: 1rem; padding: 0.6rem 0;
}
.kbv2-timeline__year {
  font-family: var(--kb-font-en-sans); font-weight: 600; color: var(--kb-amber);
  min-width: 64px;
}
.kbv2-timeline__txt {
  font-family: var(--kb-font-sans); color: var(--kb-text);
}

/* ---- Products list page ---- */
.kbv2-plist__grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 2rem;
}
@media (max-width: 700px) { .kbv2-plist__grid { grid-template-columns: 1fr; } }
.kbv2-plist__card {
  display: block; background: #FFFFFF; border: 1px solid var(--kb-line);
  border-radius: 6px; text-decoration: none; color: var(--kb-text); overflow: hidden;
  transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.kbv2-plist__card:hover { transform: translateY(-4px); box-shadow: 0 16px 32px rgba(31,42,68,0.10); }
.kbv2-plist__art {
  position: relative; aspect-ratio: 4 / 3;
  display: flex; align-items: center; justify-content: center;
}
.kbv2-plist__art--footcare-kb-fc01 {
  background: linear-gradient(135deg, #2A3654 0%, #1F2A44 60%, #15192E 100%);
}
.kbv2-plist__art--treadmill-kb-tm01 {
  background: linear-gradient(135deg, #E08A37 0%, #C96E12 100%);
}
.kbv2-plist__cat {
  position: absolute; top: 12px; left: 12px;
  background: rgba(31,42,68,0.84); color: #FFFFFF;
  font-family: var(--kb-font-en-sans); font-size: 0.7rem; letter-spacing: 0.14em;
  padding: 0.35rem 0.8rem; border-radius: 999px;
}
.kbv2-plist__body { padding: 1.4rem 1.6rem 1.8rem; }
.kbv2-plist__name {
  font-family: var(--kb-font-jp); font-weight: 600;
  font-size: 1.15rem; color: var(--kb-text); margin: 0 0 0.6rem;
}
.kbv2-plist__price {
  font-family: var(--kb-font-en-sans); font-weight: 600; font-size: 1.3rem;
  color: var(--kb-text); margin: 0 0 0.8rem;
}
.kbv2-plist__tax {
  font-family: var(--kb-font-sans); font-weight: 400; font-size: 0.74rem;
  color: var(--kb-text-sub); margin-left: 0.5rem;
}
.kbv2-plist__colors { display: flex; gap: 0.4rem; }

/* ---- Coming Soon cards ---- */
.kbv2-cs-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 2rem;
}
@media (max-width: 700px) { .kbv2-cs-grid { grid-template-columns: 1fr; } }
.kbv2-cs-card {
  background: #FFFFFF; border: 1px dashed var(--kb-navy-soft); border-radius: 6px;
  padding: 2.4rem 1.6rem; text-align: center; position: relative;
}
.kbv2-cs-card__badge {
  position: absolute; top: 16px; right: 16px;
  background: var(--kb-amber); color: #FFFFFF;
  font-family: var(--kb-font-en-sans); font-size: 0.72rem; letter-spacing: 0.14em;
  padding: 0.4rem 0.9rem; border-radius: 999px;
}
.kbv2-cs-card__en {
  font-family: var(--kb-font-en-sans); letter-spacing: 0.18em;
  font-size: 0.86rem; color: var(--kb-amber); margin: 0 0 0.4rem;
}
.kbv2-cs-card__jp {
  font-family: var(--kb-font-jp); font-weight: 600;
  font-size: 1.4rem; color: var(--kb-text); margin: 0 0 0.6rem;
}
.kbv2-cs-card__tagline {
  font-family: var(--kb-font-sans); color: var(--kb-text-sub);
  font-size: 0.95rem; margin: 0 0 1.2rem;
}
.kbv2-cs-card__schedule {
  font-family: var(--kb-font-sans); font-size: 0.84rem;
  color: var(--kb-navy); margin: 0;
  padding: 0.4rem 1rem; background: var(--kb-cream); display: inline-block; border-radius: 999px;
}

/* ---- Product detail ---- */
.kbv2-breadcrumb {
  font-family: var(--kb-font-sans); font-size: 0.84rem;
  color: var(--kb-text-sub); margin: 0 0 1.6rem;
}
.kbv2-breadcrumb a { color: var(--kb-text-sub); text-decoration: none; }
.kbv2-breadcrumb a:hover { color: var(--kb-amber); }

.kbv2-pd__top {
  display: grid; grid-template-columns: 1fr 1fr; gap: 3rem;
  align-items: start;
}
@media (max-width: 900px) { .kbv2-pd__top { grid-template-columns: 1fr; gap: 2rem; } }

.kbv2-pd__art {
  aspect-ratio: 1 / 1; border-radius: 6px;
  display: flex; align-items: center; justify-content: center;
}
.kbv2-pd__art--footcare-kb-fc01 {
  background: linear-gradient(135deg, #2A3654 0%, #1F2A44 60%, #15192E 100%);
}
.kbv2-pd__art--treadmill-kb-tm01 {
  background: linear-gradient(135deg, #E08A37 0%, #C96E12 100%);
}
.kbv2-pd__cat {
  font-family: var(--kb-font-en-sans); letter-spacing: 0.16em;
  font-size: 0.78rem; color: var(--kb-amber); margin: 0 0 0.4rem;
}
.kbv2-pd__tagline {
  font-family: var(--kb-font-jp); color: var(--kb-text-sub);
  font-size: 0.96rem; margin: 0 0 1.4rem;
}
.kbv2-pd__price {
  font-family: var(--kb-font-en-sans); font-weight: 600;
  font-size: 1.9rem; color: var(--kb-text); margin: 0 0 1.4rem;
}
.kbv2-pd__tax {
  font-family: var(--kb-font-sans); font-weight: 400; font-size: 0.82rem;
  color: var(--kb-text-sub); margin-left: 0.6rem;
}
.kbv2-pd__color-label {
  font-family: var(--kb-font-sans); font-size: 0.84rem;
  color: var(--kb-text-sub); margin: 0 0 0.6rem;
}
.kbv2-pd__colors { display: flex; gap: 0.6rem; flex-wrap: wrap; margin: 0 0 1.8rem; }

.kbv2-pd__ec-btns {
  display: flex; flex-direction: column; gap: 0.6rem; margin: 0 0 1.4rem;
}
.kbv2-ec-btn {
  display: block; padding: 0.85rem 1.4rem; border-radius: 4px;
  text-align: center; text-decoration: none; font-family: var(--kb-font-sans);
  font-size: 0.95rem; font-weight: 500; transition: all 0.2s ease;
  border: 1.5px solid var(--kb-navy);
}
.kbv2-ec-btn--amazon  { background: #FF9900; color: #FFFFFF; border-color: #FF9900; }
.kbv2-ec-btn--rakuten { background: #BF0000; color: #FFFFFF; border-color: #BF0000; }
.kbv2-ec-btn--yahoo   { background: #FF0033; color: #FFFFFF; border-color: #FF0033; }
.kbv2-ec-btn:hover { opacity: 0.88; }

.kbv2-pd__benefits {
  list-style: none; padding: 1rem 0 0; margin: 0;
  border-top: 1px solid var(--kb-line); display: flex; gap: 1rem; flex-wrap: wrap;
  font-family: var(--kb-font-sans); font-size: 0.84rem; color: var(--kb-text-sub);
}
.kbv2-pd__benefits li::before { content: "✓ "; color: var(--kb-amber); margin-right: 0.2rem; }

/* ---- Feats ---- */
.kbv2-feats {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 1.2rem;
}
@media (max-width: 900px) { .kbv2-feats { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 520px) { .kbv2-feats { grid-template-columns: 1fr; } }
.kbv2-feat {
  background: #FFFFFF; padding: 1.6rem 1rem; border-radius: 6px;
  text-align: center; border: 1px solid var(--kb-line);
}
.kbv2-feat__icon {
  font-family: var(--kb-font-en); font-size: 1.8rem; color: var(--kb-amber);
  margin-bottom: 0.6rem;
}
.kbv2-feat__title {
  font-family: var(--kb-font-jp); font-weight: 600; font-size: 0.98rem;
  color: var(--kb-text); margin: 0 0 0.4rem;
}
.kbv2-feat__sub {
  font-family: var(--kb-font-sans); font-size: 0.82rem; color: var(--kb-text-sub);
  margin: 0;
}

/* ---- Spec table ---- */
.kbv2-spec {
  width: 100%; border-collapse: collapse; margin-top: 1rem;
  font-family: var(--kb-font-sans);
}
.kbv2-spec th, .kbv2-spec td {
  padding: 0.95rem 1.2rem; border-bottom: 1px solid var(--kb-line);
  font-size: 0.92rem; text-align: left;
}
.kbv2-spec th {
  width: 32%; color: var(--kb-text-sub); font-weight: 500;
  background: var(--kb-offwhite);
}
.kbv2-spec td { color: var(--kb-text); }

/* ---- Steps ---- */
.kbv2-steps {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.6rem;
}
@media (max-width: 800px) { .kbv2-steps { grid-template-columns: 1fr; } }
.kbv2-step {
  background: #FFFFFF; padding: 1.8rem 1.4rem; border-radius: 6px;
  text-align: center; border: 1px solid var(--kb-line);
}
.kbv2-step__no {
  font-family: var(--kb-font-en); font-size: 2.2rem; color: var(--kb-amber);
  font-style: italic; margin-bottom: 0.4rem;
}
.kbv2-step__title {
  font-family: var(--kb-font-jp); font-weight: 600; font-size: 1.02rem;
  color: var(--kb-text); margin: 0 0 0.6rem;
}
.kbv2-step__body {
  font-family: var(--kb-font-sans); font-size: 0.88rem; line-height: 1.85;
  color: var(--kb-text-sub); margin: 0;
}

/* ---- Reviews ---- */
.kbv2-reviews {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.6rem;
}
@media (max-width: 800px) { .kbv2-reviews { grid-template-columns: 1fr; } }
.kbv2-review {
  background: var(--kb-offwhite); padding: 1.8rem 1.4rem; border-radius: 6px;
  border-left: 3px solid var(--kb-amber);
}
.kbv2-review__stars {
  font-family: var(--kb-font-en-sans); font-weight: 600;
  color: var(--kb-amber); margin: 0 0 0.6rem; font-size: 0.92rem;
}
.kbv2-review__body {
  font-family: var(--kb-font-jp); font-size: 0.94rem; line-height: 1.85;
  color: var(--kb-text); margin: 0;
}

/* ---- Contact ---- */
.kbv2-contact__cards {
  display: grid; grid-template-columns: repeat(3, 1fr); gap: 1.6rem;
}
@media (max-width: 800px) { .kbv2-contact__cards { grid-template-columns: 1fr; } }
.kbv2-contact__card {
  background: var(--kb-offwhite); padding: 2rem 1.6rem; border-radius: 6px;
  text-align: center; border: 1px solid var(--kb-line);
}
.kbv2-contact__icon {
  font-family: var(--kb-font-en); font-size: 2rem; color: var(--kb-amber);
  margin-bottom: 0.6rem;
}
.kbv2-contact__title {
  font-family: var(--kb-font-jp); font-weight: 600; font-size: 1rem;
  color: var(--kb-text); margin: 0 0 0.6rem;
}
.kbv2-contact__body {
  font-family: var(--kb-font-sans); font-size: 0.88rem; color: var(--kb-text-sub);
  margin: 0; line-height: 1.85;
}

.kbv2-faq { max-width: 760px; margin: 0 auto; }
.kbv2-faq__item {
  background: #FFFFFF; border: 1px solid var(--kb-line); border-radius: 6px;
  margin-bottom: 0.8rem; padding: 0;
}
.kbv2-faq__q {
  font-family: var(--kb-font-jp); font-weight: 600; font-size: 0.98rem;
  color: var(--kb-text); padding: 1.1rem 1.4rem; cursor: pointer;
  list-style: none;
}
.kbv2-faq__q::-webkit-details-marker { display: none; }
.kbv2-faq__q::before {
  content: "+"; color: var(--kb-amber); margin-right: 0.6rem; font-weight: 600;
}
.kbv2-faq__item[open] .kbv2-faq__q::before { content: "−"; }
.kbv2-faq__a {
  font-family: var(--kb-font-sans); font-size: 0.92rem; line-height: 1.95;
  color: var(--kb-text-sub); padding: 0 1.4rem 1.2rem; margin: 0;
}

.kbv2-mail-box {
  background: var(--kb-offwhite); padding: 2rem 1.6rem;
  border-radius: 6px; text-align: center;
  font-family: var(--kb-font-sans); color: var(--kb-text-sub);
}
.kbv2-mail-box p { margin: 0.4rem 0; font-size: 0.92rem; }
.kbv2-mail-box__addr {
  font-family: var(--kb-font-en-sans); font-weight: 600;
  font-size: 1.2rem; color: var(--kb-text); letter-spacing: 0.04em;
  margin: 1rem 0 !important;
}
.kbv2-mail-box__meta { font-size: 0.82rem !important; }

/* ---- Legal ---- */
.kbv2-legal {
  width: 100%; border-collapse: collapse; margin-top: 1rem;
  font-family: var(--kb-font-sans);
}
.kbv2-legal th, .kbv2-legal td {
  padding: 0.95rem 1.2rem; border-bottom: 1px solid var(--kb-line);
  font-size: 0.92rem; text-align: left; vertical-align: top;
}
.kbv2-legal th {
  width: 32%; color: var(--kb-text-sub); font-weight: 500;
  background: var(--kb-offwhite);
}
.kbv2-legal__h {
  font-family: var(--kb-font-jp); font-weight: 600; font-size: 1rem;
  color: var(--kb-text); margin: 1.6rem 0 0.6rem;
}
.kbv2-legal__p {
  font-family: var(--kb-font-sans); font-size: 0.92rem; line-height: 1.95;
  color: var(--kb-text-sub); margin: 0;
}

/* ---- SWELL 邪魔抑止 ---- */
#main-visual, .p-mainVisual, .l-mainVisual, .p-mainVisual__inner {
  display: none !important;
}
.l-sidebar, #sidebar, .p-blogSidebar { display: none !important; }
.l-content, .l-main, #main, .l-mainContent { padding: 0 !important; max-width: 100% !important; }
.post_content { padding: 0 !important; margin: 0 !important; max-width: none !important; }
.p-blogParts__inner { padding: 0 !important; }
/* SWELL の H2 デフォルト装飾抑止（広域） */
.post_content h1, .post_content h2, .post_content h3 {
  background: transparent !important; border: 0 !important;
  padding-left: 0 !important; padding-right: 0 !important;
}
"""
