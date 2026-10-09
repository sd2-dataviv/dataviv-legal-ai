"""RFC 9457 problem+json errors."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status = 400
    title = "Bad request"

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(AppError):
    """Also used for objects the principal may not access (ADR-007: never reveal existence)."""

    status = 404
    title = "Not found"


class Forbidden(AppError):
    """Only for actions on objects the principal CAN see but may not change."""

    status = 403
    title = "Forbidden"


class Unauthorised(AppError):
    status = 401
    title = "Unauthorised"


class Conflict(AppError):
    status = 409
    title = "Conflict"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            media_type="application/problem+json",
            content={"type": "about:blank", "title": exc.title, "status": exc.status, "detail": exc.detail},
        )
