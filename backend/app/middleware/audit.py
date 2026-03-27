import time
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)


class AuditMiddleware(BaseHTTPMiddleware):
    """请求日志中间件 — 记录每个 API 请求的耗时"""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = round((time.time() - start) * 1000, 2)

        logger.info(
            f"{request.method} {request.url.path} → {response.status_code} ({duration}ms)"
        )

        return response
