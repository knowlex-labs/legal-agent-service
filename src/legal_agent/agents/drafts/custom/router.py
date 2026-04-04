"""FastAPI routes for custom drafting templates.

Registered at /api/v1/drafts/templates in main.py.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from legal_agent.agents.drafts.custom.models import CreateTemplateRequest, TemplateResponse
from legal_agent.agents.drafts.custom.service import TemplateService

logger = logging.getLogger(__name__)

router = APIRouter()

_template_service: TemplateService | None = None


def set_template_service(service: TemplateService) -> None:
    global _template_service
    _template_service = service


def get_template_service() -> TemplateService:
    if _template_service is None:
        raise RuntimeError("TemplateService not initialized")
    return _template_service


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    request: CreateTemplateRequest,
    x_user_id: str = Header(..., alias="X-User-Id"),
    service: TemplateService = Depends(get_template_service),
) -> TemplateResponse:
    """Register a new custom drafting template for the user."""
    logger.info(
        f"POST /drafts/templates: user={x_user_id} name='{request.name}' type='{request.document_type}'"
    )
    return await service.create_template(request, user_id=x_user_id)


@router.get("", response_model=list[TemplateResponse])
async def list_templates(
    x_user_id: str = Header(..., alias="X-User-Id"),
    service: TemplateService = Depends(get_template_service),
) -> list[TemplateResponse]:
    """List all custom templates belonging to the user."""
    return await service.list_templates(user_id=x_user_id)


@router.get("/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: UUID,
    x_user_id: str = Header(..., alias="X-User-Id"),
    service: TemplateService = Depends(get_template_service),
) -> TemplateResponse:
    """Get a single custom template by ID."""
    return await service.get_template(str(template_id), user_id=x_user_id)


@router.delete("/{template_id}", status_code=204)
async def delete_template(
    template_id: UUID,
    x_user_id: str = Header(..., alias="X-User-Id"),
    service: TemplateService = Depends(get_template_service),
) -> None:
    """Delete a custom template."""
    logger.info(f"DELETE /drafts/templates/{template_id}: user={x_user_id}")
    await service.delete_template(str(template_id), user_id=x_user_id)
