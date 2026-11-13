from __future__ import annotations

from fastapi import APIRouter

from app.deps import AdminDep, DbSession
from app.schemas.admin import LayerInfo, PipelineStatus, ScoringConfigModel
from app.schemas.common import error_responses
from app.services import admin as admin_service

router = APIRouter(prefix="/admin", tags=["Admin"], responses=error_responses(401, 403, 503))


@router.get("/layers", response_model=list[LayerInfo], summary="Data layers and provenance")
def layers(_admin: AdminDep, db: DbSession) -> list[LayerInfo]:
    return admin_service.list_layers(db)


@router.get("/config", response_model=ScoringConfigModel, summary="Active scoring configuration",
            responses=error_responses(404))
def read_config(_admin: AdminDep, db: DbSession) -> ScoringConfigModel:
    return admin_service.get_active_config(db)


@router.put("/config", response_model=ScoringConfigModel, summary="Save a new scoring configuration version (not built)",
            responses=error_responses(422, 501))
def update_config(payload: ScoringConfigModel, admin: AdminDep, db: DbSession) -> ScoringConfigModel:
    return admin_service.save_config(db, payload, created_by=str(admin.user_id))


@router.get("/pipeline/status", response_model=PipelineStatus, summary="Last pipeline run")
def pipeline_status(_admin: AdminDep, db: DbSession) -> PipelineStatus:
    return admin_service.get_pipeline_status(db)
