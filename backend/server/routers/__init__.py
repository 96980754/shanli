import os

from fastapi import APIRouter

from server.routers.agent_invocation_router import agent_invocation_router
from server.routers.agent_router import agent_router
from server.routers.auth_dept_router import department
from server.routers.auth_router import auth
from server.routers.chat_router import chat
from server.routers.dashboard_router import dashboard
from server.routers.feedback_dashboard_router import feedback_dashboard
from server.routers.filesystem_router import filesystem_router
from server.routers.mcp_router import mcp
from server.routers.mention_router import mention_router
from server.routers.model_provider_router import model_providers
from server.routers.skill_router import skills, user_skills
from server.routers.system_router import system
from server.routers.system_task_router import tasks
from server.routers.tool_router import tools
from server.routers.user_router import user_router
from server.routers.workspace_router import workspace
from server.routers.wecom_router import wecom

_LITE_MODE = os.environ.get("LITE_MODE", "").lower() in ("true", "1")

router = APIRouter()

router.include_router(system)
router.include_router(auth)
router.include_router(agent_router)
router.include_router(agent_invocation_router)
router.include_router(chat)
router.include_router(dashboard)
router.include_router(feedback_dashboard)
router.include_router(department)
router.include_router(tasks)
router.include_router(mcp)
router.include_router(model_providers)
router.include_router(skills)
router.include_router(user_skills)
router.include_router(tools)
router.include_router(user_router)
router.include_router(filesystem_router)
router.include_router(workspace)
router.include_router(mention_router)
router.include_router(wecom)

if not _LITE_MODE:
    from server.routers.graph_router import graph
    from server.routers.knowledge_eval_router import evaluation
    from server.routers.knowledge_router import knowledge
    from server.routers.ontology_registry_router import ontology_registries

    router.include_router(ontology_registries)
    router.include_router(knowledge)
    router.include_router(evaluation)
    router.include_router(graph)
