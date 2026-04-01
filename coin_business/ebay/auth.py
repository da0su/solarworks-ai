"""
coin_business/ebay/auth.py
============================
eBay OAuth2 トークン管理モジュール。

Client Credentials Grant フロー:
  1. EBAY_CLIENT_ID + EBAY_CLIENT_SECRET から Base64 認証ヘッダーを生成
  2. eBay Identity API でアクセストークン取得
  3. 有効期限付きでメモリキャッシュ (デフォルト 7200 秒)
  4. 期限の 60 秒前に自動更新

このトークンは public スコープ用 (Browse API / Finding API 検索)。
ユーザー認証は不要。
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# ================================================================
# eBay API エンドポイント
# ================================================================

TOKEN_URL       = "https://api.ebay.com/identity/v1/oauth2/token"
PUBLIC_SCOPE    = "https://api.ebay.com/oauth/api_scope"
EXPIRY_BUFFER_S = 60    # 有効期限 N 秒前に再取得
DEFAULT_TIMEOUT = 15    # HTTP タイムアウト (秒)


# ================================================================
# EbayTokenManager
# ================================================================

class EbayTokenManager:
    """
    eBay Client Credentials トークンをキャッシュ管理する。

    Usage:
        mgr = EbayTokenManager()
        token = mgr.get_token()   # 有効なトークンを返す (自動更新)

    Args:
        client_id:     EBAY_CLIENT_ID 環境変数（省略時は自動読み込み）
        client_secret: EBAY_CLIENT_SECRET 環境変数（省略時は自動読み込み）
        scope:         OAuth2 スコープ（デフォルト: PUBLIC_SCOPE）
    """

    def __init__(
        self,
        client_id:     str | None = None,
        client_secret: str | None = None,
        scope:         str = PUBLIC_SCOPE,
    ):
        # 環境変数フォールバック
        self._client_id     = client_id     or os.environ.get("EBAY_CLIENT_ID", "")
        self._client_secret = client_secret or os.environ.get("EBAY_CLIENT_SECRET", "")
        self._scope         = scope

        # キャッシュ
        self._token:   str | None      = None
        self._expires: datetime | None = None

        if not self.is_configured:
            logger.warning(
                "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET が未設定。"
                "eBay API 呼び出しは機能しません。"
            )

    # ----------------------------------------------------------------
    # Properties
    # ----------------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """クライアント ID とシークレットが両方設定されているか。"""
        return bool(self._client_id and self._client_secret)

    @property
    def is_token_valid(self) -> bool:
        """キャッシュトークンが有効か。"""
        if not self._token or not self._expires:
            return False
        return datetime.now(timezone.utc) < self._expires

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def get_token(self) -> str:
        """
        有効なアクセストークンを返す。
        キャッシュが有効なら再利用、期限切れなら再取得する。

        Returns:
            アクセストークン文字列

        Raises:
            RuntimeError: 未設定 or API エラー
        """
        if not self.is_configured:
            raise RuntimeError(
                "eBay API が未設定。EBAY_CLIENT_ID / EBAY_CLIENT_SECRET を .env に設定してください。"
            )

        if self.is_token_valid:
            return self._token  # type: ignore

        return self._fetch_token()

    def invalidate(self) -> None:
        """キャッシュを強制破棄する（テスト用）。"""
        self._token   = None
        self._expires = None

    # ----------------------------------------------------------------
    # Internal
    # ----------------------------------------------------------------

    def _fetch_token(self) -> str:
        """eBay Identity API からトークンを取得してキャッシュする。"""
        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        try:
            resp = requests.post(
                TOKEN_URL,
                headers={
                    "Content-Type":  "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}",
                },
                data={
                    "grant_type": "client_credentials",
                    "scope":      self._scope,
                },
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            logger.error("eBay token 取得失敗 (HTTP %s): %s", exc.response.status_code, exc)
            raise RuntimeError(f"eBay token 取得失敗: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            logger.error("eBay token 取得失敗 (Network): %s", exc)
            raise RuntimeError(f"eBay token 取得失敗: {exc}") from exc

        data       = resp.json()
        token      = data.get("access_token", "")
        expires_in = int(data.get("expires_in", 7200))

        if not token:
            raise RuntimeError(f"eBay token レスポンスが空: {data}")

        self._token   = token
        self._expires = (
            datetime.now(timezone.utc)
            + timedelta(seconds=max(expires_in - EXPIRY_BUFFER_S, 60))
        )
        logger.debug("eBay token 取得成功 (expires_in=%ds)", expires_in)
        return self._token
