"""環境変数ベースの設定値。

レート制限は以下の環境変数で調整できる:
  RATE_LIMIT_ENABLED         - "false" にすると無効化 (既定: true)
  RATE_LIMIT_REQUESTS        - ウィンドウ内の許可リクエスト数 (既定: 120)
  RATE_LIMIT_WINDOW_SECONDS  - ウィンドウの長さ（秒） (既定: 60)

既定値は通常利用（オートコンプリートの連続入力含む）では
まず引っかからない、十分に緩い値にしている。
"""
import os


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


RATE_LIMIT_ENABLED        = _bool_env("RATE_LIMIT_ENABLED", True)
RATE_LIMIT_REQUESTS       = _int_env("RATE_LIMIT_REQUESTS", 120)
RATE_LIMIT_WINDOW_SECONDS = _int_env("RATE_LIMIT_WINDOW_SECONDS", 60)
