"""
SEO 記事生成 — Claude API (anthropic)

■ 記事仕様:
  - 3000〜4000字（HTML形式）
  - H1 + H2×6〜7 + CTA×3箇所
  - 2026年表記統一
  - タイナビA8リンク [LINK-T01] を3箇所のCTAに挿入

■ キーワードタイプ別テンプレート:
  - 行動系: 補助金申請手順ガイド形式
  - 比較系: TOP3比較表 + 詳細レビュー形式
  - 不安系: 不安解消 + 費用解説形式
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

import anthropic

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
TABINAYI_LINK = os.getenv("TABINAYI_LINK", "https://px.a8.net/svt/ejp?a8mat=XXXXXXXX_REPLACE_ME")
MODEL = "claude-haiku-4-5-20251001"  # 速度重視。品質優先時は claude-sonnet-4-5-20250929 に変更

# ── CTAテンプレート（pre_approval_prep_v1.md より） ────────────────────

CTA_URGENT = f"""<div class="cta-box urgent" style="background:#fff3cd;border:2px solid #ffc107;border-radius:8px;padding:20px;margin:30px 0;text-align:center;">
  <p style="font-weight:bold;color:#d9534f;font-size:1.1em;">⚠️ 2026年CEV補助金 申請受付中</p>
  <p style="font-size:1.2em;">補助金を使えば最大<strong>130万円</strong>お得に導入できます</p>
  <p style="color:#555;">無料で相見積もりをとって損はなし。業者が決まっていなくてもOK。</p>
  <a href="{TABINAYI_LINK}" style="display:inline-block;background:#d9534f;color:#fff;padding:14px 32px;border-radius:6px;font-size:1.1em;text-decoration:none;margin-top:10px;">▶ 無料で見積もりを依頼する（タイナビ）</a>
  <p style="color:#888;font-size:0.85em;margin-top:8px;">※ 申請は施工業者を通じて行います。まず見積もりが必要です。</p>
</div>"""

CTA_COMPARE = f"""<div class="cta-box compare" style="background:#e8f4f8;border:2px solid #17a2b8;border-radius:8px;padding:20px;margin:30px 0;text-align:center;">
  <p style="font-size:1.2em;">複数社に見積もりを依頼すると<strong>平均20〜30万円</strong>安くなります</p>
  <p style="color:#555;">タイナビなら最大5社まで無料で比較できます（営業電話なし）</p>
  <a href="{TABINAYI_LINK}" style="display:inline-block;background:#17a2b8;color:#fff;padding:14px 32px;border-radius:6px;font-size:1.1em;text-decoration:none;margin-top:10px;">▶ 無料で複数社を比較する</a>
</div>"""

CTA_FINAL = f"""<div class="cta-box final" style="background:#d4edda;border:2px solid #28a745;border-radius:8px;padding:20px;margin:30px 0;text-align:center;">
  <p style="font-size:1.2em;font-weight:bold;">まだ業者が決まっていない方へ</p>
  <ul style="text-align:left;display:inline-block;margin:10px 0;">
    <li>✅ 無料・3分で完了</li>
    <li>✅ 全国の優良施工業者のみ</li>
    <li>✅ 見積もり後にキャンセル可</li>
  </ul>
  <br>
  <a href="{TABINAYI_LINK}" style="display:inline-block;background:#28a745;color:#fff;padding:14px 32px;border-radius:6px;font-size:1.1em;text-decoration:none;margin-top:10px;">▶ 無料見積もりを依頼する（公式）</a>
</div>"""


def _build_prompt(keyword: str, theme: str, kw_type: str) -> str:
    """キーワードタイプ別のプロンプトを構築"""

    cta_placeholder_note = (
        "記事内のCTA（行動喚起）は以下の3箇所に必ず挿入してください:\n"
        "  [CTA1] 冒頭〜比較表直後\n"
        "  [CTA2] 詳細説明の後半\n"
        "  [CTA3] まとめの直後（末尾）\n"
        "CTAの場所に <!-- CTA1 --> <!-- CTA2 --> <!-- CTA3 --> というHTMLコメントを入れてください。\n"
    )

    if kw_type == "行動系":
        structure = """
記事構成（行動系 — 補助金・申請ガイド型）:
H1: 【2026年最新】{keyword}｜完全ガイド
## はじめに（300字）— 読者の状況・この記事でわかること
<!-- CTA1 -->
## 2026年の補助金制度まとめ
## 申請条件・対象者
## ステップ別申請方法（図解風リスト）
## 申請でよくある失敗・注意点
<!-- CTA2 -->
## 補助金を使った場合の費用シミュレーション
## よくある質問（FAQ）3〜5件
## まとめ
<!-- CTA3 -->
"""
    elif kw_type == "比較系":
        structure = """
記事構成（比較系 — TOP3比較表型）:
H1: 【2026年最新】{keyword}｜TOP3比較+専門家が選ぶポイント解説
## はじめに（300字）— 選ぶ際の悩みと本記事の目的
<!-- CTA1 -->
## 1〜3位 早見比較表（HTML tableタグで作成）
## 1位: [製品/メーカー名] — 詳細レビュー（特徴/価格/メリット/デメリット/向いている人）
## 2位: [製品/メーカー名] — 詳細レビュー
## 3位: [製品/メーカー名] — 詳細レビュー
<!-- CTA2 -->
## 選び方のポイント（チェックリスト）
## 2026年 補助金を活用した実質費用
## よくある質問（FAQ）3件
## まとめ
<!-- CTA3 -->
"""
    else:  # 不安系
        structure = """
記事構成（不安系 — 不安解消・費用解説型）:
H1: 【2026年版】{keyword}｜費用の相場と失敗しない選び方
## はじめに（300字）— 読者の不安を共感・本記事の目的
<!-- CTA1 -->
## 費用の内訳・相場（具体的数字を明示）
## 高い/安いを分けるポイント
## 費用を安くする方法（補助金・相見積もり）
<!-- CTA2 -->
## 失敗しない業者の選び方
## 実際の費用シミュレーション例
## よくある質問（FAQ）3件
## まとめ
<!-- CTA3 -->
"""

    structure = structure.replace("{keyword}", keyword)

    return f"""あなたは太陽光発電・V2H・蓄電池専門のSEOライターです。
以下の条件で日本語のSEO記事をHTML形式で作成してください。

## 対象キーワード
{keyword}

## テーマ・カテゴリ
{theme}（{kw_type}）

## 必須条件
- 文字数: 3000〜4000字（本文のみ）
- 年号: すべて「2026年」表記（2025年は使用禁止）
- 形式: HTML（h1/h2/h3/p/ul/li/table/strong タグを適切に使用）
- h1タグは1つのみ。h2は6〜8個
- 数値・データは具体的に記載（「約〇〇万円」「〇〇%増」など）
- 太陽光・V2H・蓄電池の専門知識を活用した信頼性の高い内容
- 読者は「導入を検討している一般消費者」を想定

## CTAの挿入（必須）
{cta_placeholder_note}

## 記事構成
{structure}

## SEO最適化
- タイトル（h1）にキーワードを自然に含める
- 各h2見出しに関連語を含める
- 内部リンク用のアンカーテキストを自然に配置（href="#"でOK）
- メタディスクリプション用の要約文（120字以内）を最後に <!-- META: ～ --> 形式で出力

記事のHTMLのみを出力してください。前置き・後書き・説明は不要です。"""


def generate_article(keyword: str, theme: str, kw_type: str) -> dict:
    """
    Claude APIで記事生成。

    Returns:
        {"title": str, "content": str, "meta_desc": str, "success": bool, "error": str}
    """
    if not ANTHROPIC_API_KEY:
        return {"success": False, "error": "ANTHROPIC_API_KEY未設定", "title": "", "content": "", "meta_desc": ""}

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = _build_prompt(keyword, theme, kw_type)

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=6000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw_html = message.content[0].text.strip()

        # CTA プレースホルダーを実際のCTAに置換
        raw_html = raw_html.replace("<!-- CTA1 -->", CTA_URGENT)
        raw_html = raw_html.replace("<!-- CTA2 -->", CTA_COMPARE)
        raw_html = raw_html.replace("<!-- CTA3 -->", CTA_FINAL)

        # フォールバック: <!-- CTA3 --> が挿入されなかった場合は末尾に追加
        if CTA_FINAL not in raw_html:
            raw_html += f"\n{CTA_FINAL}"

        # フォールバック: CTA1が無ければ冒頭に追加
        if CTA_URGENT not in raw_html:
            h1_match = re.search(r'</h1>', raw_html, re.IGNORECASE)
            if h1_match:
                pos = h1_match.end()
                raw_html = raw_html[:pos] + f"\n{CTA_URGENT}" + raw_html[pos:]

        # タイトル抽出（h1タグから）
        title_match = re.search(r'<h1[^>]*>(.*?)</h1>', raw_html, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1) if title_match else f"【2026年最新】{keyword}"
        title = re.sub(r'<[^>]+>', '', title).strip()

        # メタディスクリプション抽出
        meta_match = re.search(r'<!--\s*META:\s*(.*?)\s*-->', raw_html, re.IGNORECASE | re.DOTALL)
        meta_desc = meta_match.group(1).strip() if meta_match else f"{keyword}について2026年最新情報をわかりやすく解説。補助金・費用・業者選びのポイントを専門家目線でまとめました。"
        # メタ行をHTMLから削除
        raw_html = re.sub(r'<!--\s*META:.*?-->', '', raw_html, flags=re.DOTALL)

        return {
            "success": True,
            "title": title,
            "content": raw_html,
            "meta_desc": meta_desc[:160],
            "error": "",
        }

    except anthropic.APIError as e:
        return {"success": False, "error": f"APIError: {e}", "title": "", "content": "", "meta_desc": ""}
    except Exception as e:
        return {"success": False, "error": f"Exception: {e}", "title": "", "content": "", "meta_desc": ""}


if __name__ == "__main__":
    # テスト生成
    result = generate_article("V2H 補助金 2026 申請方法", "V2H", "行動系")
    if result["success"]:
        print(f"Title: {result['title']}")
        print(f"Meta: {result['meta_desc']}")
        print(f"Content length: {len(result['content'])} chars")
        print(result['content'][:500])
    else:
        print(f"ERROR: {result['error']}")
