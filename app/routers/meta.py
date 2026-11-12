from __future__ import annotations

from fastapi import APIRouter

from app.deps import CurrentUserDep, DbSession
from app.schemas.meta import MetaConfigResponse
from app.services import admin as admin_service

router = APIRouter(prefix="/meta", tags=["Meta"])


@router.get("/config", response_model=MetaConfigResponse, summary="Tier definitions and weights for the UI")
def meta_config(_user: CurrentUserDep, db: DbSession) -> MetaConfigResponse:
    return admin_service.get_public_meta(db)
