from __future__ import annotations

from fastapi import APIRouter

from app.deps import CurrentUserDep, DbSession
from app.schemas.analysis import AnalysisMetadataResponse
from app.schemas.common import error_responses
from app.services import analysis as analysis_service

router = APIRouter(prefix="/analysis", tags=["Analysis"])


@router.get("/metadata", response_model=AnalysisMetadataResponse, summary="How the stored results were produced",
            responses=error_responses(401, 503))
def analysis_metadata(_user: CurrentUserDep, db: DbSession) -> AnalysisMetadataResponse:
    """Model version, configuration (weights, class boundaries, category ranges), analysis date, the run
    that produced the results, how many roads were analysed, data flags, data sources and the statement
    of what the index is not."""
    return analysis_service.get_analysis_metadata(db)
