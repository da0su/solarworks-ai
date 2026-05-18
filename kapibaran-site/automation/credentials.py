# -*- coding: utf-8 -*-
"""pass/ フォルダの認証ファイルから WP 認証情報を読み込む。

SSOT: C:\\Users\\infoa\\Box\\会長\\39.AI-COMPANY\\01_SECRETARY\\pass\\www.kapibaran.com.txt
パスワードはチャットやログに出さない。スクリプト内のみで使う。
"""
from pathlib import Path
import re

PASS_FILE = Path(r"C:\Users\infoa\Box\会長\39.AI-COMPANY\01_SECRETARY\pass\www.kapibaran.com.txt")


def load() -> dict:
    if not PASS_FILE.exists():
        raise FileNotFoundError(f"認証ファイルが見つかりません: {PASS_FILE}")
    text = PASS_FILE.read_text(encoding="utf-8")
    creds = {"site_url": "https://www.kapibaran.com/", "login_url": "https://www.kapibaran.com/wp-login.php"}
    # ID_xxx / pass_xxx 形式を拾う
    m_id = re.search(r"^ID_(\S+)", text, re.MULTILINE)
    m_pw = re.search(r"^pass_(\S+)", text, re.MULTILINE)
    if not m_id or not m_pw:
        raise ValueError("認証ファイルから ID/PASS を抽出できません")
    creds["username"] = m_id.group(1).strip()
    creds["password"] = m_pw.group(1).strip()
    # SWELL email
    m_email = re.search(r"^([\w\.\-]+@[\w\.\-]+\.\w+)", text, re.MULTILINE)
    if m_email:
        creds["swell_email"] = m_email.group(1).strip()
    return creds


if __name__ == "__main__":
    c = load()
    masked = {k: (v if k not in {"password"} else "***" + v[-3:]) for k, v in c.items()}
    print(masked)
