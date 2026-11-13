from __future__ import annotations

from fastapi import APIRouter

from app.deps import CurrentUserDep, DbSession
from app.schemas.common import error_responses
from app.schemas.meta import MetaConfigResponse
from app.services import admin as admin_service

router = APIRouter(prefix="/meta", tags=["Meta"])


@router.get("/config", response_model=MetaConfigResponse, summary="Category ranges and weights for the legend",
            responses=error_responses(401, 409, 503))
def meta_config(_user: CurrentUserDep, db: DbSession) -> MetaConfigResponse:
    return admin_service.get_public_meta(db)
