from fastapi import APIRouter, Request

from ..dependencies import require_admin
from ...repositories.audit import list_admin_actions


router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def audit_log(
    request: Request, limit: int = 100, actor_login: str = "", entity_type: str = "",
):
    require_admin(request)
    return list_admin_actions(
        limit=limit, actor_login=actor_login.strip() or None,
        entity_type=entity_type.strip() or None,
    )
