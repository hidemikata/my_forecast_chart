import logging
import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import config

logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """IP単位のスライディングウィンドウ方式レート制限（/api/* のみ対象）。

    プロセス内メモリで状態を持つため、複数ワーカープロセスで動かす場合は
    ワーカーごとに別カウントになる点に注意（本アプリはシングルプロセス運用を想定）。
    """

    def __init__(self, app):
        super().__init__(app)
        self._max = config.RATE_LIMIT_REQUESTS
        self._window = config.RATE_LIMIT_WINDOW_SECONDS
        self._enabled = config.RATE_LIMIT_ENABLED
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()
        if self._enabled:
            logger.info(
                "Rate limit enabled: %d req / %ds per IP (on /api/*)",
                self._max, self._window,
            )

    async def dispatch(self, request: Request, call_next):
        if not self._enabled or not request.url.path.startswith("/api/"):
            return await call_next(request)

        ip = request.client.host if request.client else "unknown"
        now = time.monotonic()

        with self._lock:
            hits = self._hits[ip]
            while hits and now - hits[0] > self._window:
                hits.popleft()
            if len(hits) >= self._max:
                retry_after = max(1, int(self._window - (now - hits[0])) + 1)
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "リクエストが多すぎます。しばらく待ってから再度お試しください。",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)

        return await call_next(request)
