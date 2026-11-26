import re

from core.config import MAX_FILE_SIZE_BYTES, MULTIPART_OVERHEAD_BYTES

_DOCUMENT_UPLOAD_PATH_RE = re.compile(r"^/api/customers/\d+/documents/?$")


class DocumentUploadSizeGuard:
    # Middleware ASGI puro: rechaza con 413 las subidas cuyo Content-Length ya excede el limite,
    # antes de que el servidor reciba y guarde en disco todo el cuerpo.
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and _DOCUMENT_UPLOAD_PATH_RE.match(scope["path"])
        ):
            for header_name, header_value in scope.get("headers", []):
                if header_name == b"content-length":
                    try:
                        declared_length = int(header_value)
                    except ValueError:
                        declared_length = 0
                    if declared_length > MAX_FILE_SIZE_BYTES + MULTIPART_OVERHEAD_BYTES:
                        body = b'{"detail":"File must be between 1 byte and 10 MB."}'
                        await send({
                            "type": "http.response.start", "status": 413,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode("ascii")),
                            ],
                        })
                        await send({"type": "http.response.body", "body": body})
                        return
                    break
        await self.app(scope, receive, send)
