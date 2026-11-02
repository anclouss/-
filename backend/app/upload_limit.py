"""Bound ingress before multipart spooling, with a small envelope allowance."""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .config import MAX_BYTES


class UploadLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/projects/upload":
            return await self.app(scope, receive, send)
        maximum = MAX_BYTES + 2 * 1024**2
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await JSONResponse({"detail": "Некорректный размер загрузки"}, 400)(
                scope, receive, send
            )
        if length > maximum:
            return await JSONResponse({"detail": "Видео превышает ограничение загрузки"}, 413)(
                scope, receive, send
            )
        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    raise HTTPException(413, "Видео превышает ограничение загрузки")
            return message

        return await self.app(scope, bounded_receive, send)
