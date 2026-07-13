from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json

from fastapi import FastAPI, Header, HTTPException, UploadFile, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.db import create_session_factory
from app.models import ContentBlock, DocumentChunk, ParseTask
from app.services.auth_service import AuthService
from app.services.db_chunk_loader import DbChunkLoader
from app.services.db_document_service import DbDocumentService
from app.services.db_kb_service import DbKnowledgeBaseService
from app.services.document_service import InMemoryDocumentService
from app.services.document_filter_service import DocumentFilterService
from app.services.file_storage import LocalFileStorageService
from app.services.ingestion_service import IngestionService
from app.services.kb_service import InMemoryKnowledgeBaseService
from app.services.rag_service import RAGService
from app.services.session_store import SessionStore
from app.services.qa_ops_service import QaOpsService
from app.services.retrieval_policy import RetrievalPolicy
from app.services.view_rule_service import KnowledgeViewRuleService

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOGIN_HTML = STATIC_DIR / "login.html"
ADMIN_HTML = STATIC_DIR / "admin.html"
QA_HTML = STATIC_DIR / "qa.html"


class SimpleLLM:
    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        stream: bool = False,
    ) -> dict[str, Any]:
        if tools and tool_choice == "auto":
            user_message = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return {
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "retrieve",
                        "arguments": {"query": user_message, "top_k": 5},
                    }
                ]
            }
        return {"content": "SOS 报警可以在设置中关闭。", "tool_calls": []}


class AskRequest(BaseModel):
    question: str
    kb_id: str
    conversation_id: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: str = ""
    visibility: str = "department"


class KnowledgeBaseUpdate(BaseModel):
    name: str
    description: str = ""
    visibility: str = "department"


class KnowledgeBasePermissionUpdate(BaseModel):
    can_view: bool = False
    can_upload: bool = False
    can_delete: bool = False
    can_grant: bool = False


class QaFeedbackRequest(BaseModel):
    message_id: int
    is_helpful: bool
    feedback_text: str = ""


class KnowledgeIssueUpdate(BaseModel):
    status: str


class KnowledgeViewRuleUpdate(BaseModel):
    allowed_departments: list[str] = Field(default_factory=list)
    allowed_product_lines: list[str] = Field(default_factory=list)
    allowed_visibilities: list[str] = Field(default_factory=list)
    max_security_level: int | None = None


def require_session(session_store: SessionStore, authorization: str | None) -> dict[str, str]:
    if authorization is None:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Invalid authorization header")

    session = session_store.get(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid session token")
    return session


def require_kb_permission(app: FastAPI, kb_id: str | int, user_id: str, permission: str) -> None:
    if not app.state.kb_service.has_permission(kb_id, user_id, permission):
        raise HTTPException(status_code=403, detail="Permission denied")


def resolve_session_user_id(app: FastAPI, session: dict[str, str]) -> str | int:
    user_id = session["user_id"]
    if app.state.service_mode == "database":
        if user_id.isdigit():
            return int(user_id)
        default_owner_id = app.state.default_owner_id
        if default_owner_id is not None and session.get("username") == "admin":
            return default_owner_id
    return user_id


def build_app_state_services(
    mode: str = "memory",
    session: Any | None = None,
    session_factory: Any | None = None,
) -> dict[str, Any]:
    if mode == "database":
        if session is None:
            if session_factory is None:
                raise ValueError("database mode requires a session or session_factory")
            session = session_factory()
        kb_service = DbKnowledgeBaseService(session)
        document_service = DbDocumentService(session)
        return {
            "mode": "database",
            "kb_service": kb_service,
            "document_service": document_service,
            "qa_ops_service": QaOpsService(session),
            "view_rule_service": KnowledgeViewRuleService(session),
            "session": session,
        }

    kb_service = InMemoryKnowledgeBaseService()
    document_service = InMemoryDocumentService(kb_service)
    return {
        "mode": "memory",
        "kb_service": kb_service,
        "document_service": document_service,
        "qa_ops_service": None,
        "view_rule_service": None,
    }


def create_app(
    mode: str = "memory",
    session: Any | None = None,
    session_factory: Any | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Knowledge Base")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.state.rag_service = RAGService(llm=SimpleLLM())
    app.state.session_store = SessionStore()
    app.state.auth_service = AuthService(app.state.session_store)
    services = build_app_state_services(mode=mode, session=session, session_factory=session_factory)
    app.state.service_mode = services["mode"]
    app.state.kb_service = services["kb_service"]
    app.state.document_service = services["document_service"]
    app.state.qa_ops_service = services["qa_ops_service"]
    app.state.view_rule_service = services["view_rule_service"]
    app.state.document_filter_service = (
        DocumentFilterService(app.state.kb_service, app.state.view_rule_service)
        if services["mode"] == "database"
        else None
    )
    app.state.review_queue = []
    app.state.conflict_log = []
    app.state.parse_tasks = []
    app.state.knowledge_issues = []
    app.state.default_owner_id = None
    app.state.upload_root = Path("uploads")
    app.state.file_storage_root = Path("uploads/files")
    app.state.retrieval_policy_path = Path(__file__).resolve().parents[1] / "config" / "retrieval_policy.yaml"
    app.state.rag_service.tools.set_retrieval_policy_path(app.state.retrieval_policy_path)
    if services["mode"] == "database":
        app.state.db_session = services["session"]
    register_routes(app)
    return app


def register_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    def login_page() -> str:
        return LOGIN_HTML.read_text(encoding="utf-8")

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page() -> str:
        return ADMIN_HTML.read_text(encoding="utf-8")

    @app.get("/qa", response_class=HTMLResponse)
    def qa_page() -> str:
        return QA_HTML.read_text(encoding="utf-8")

    @app.post("/api/auth/login")
    def login(request: LoginRequest) -> dict[str, str]:
        token = app.state.auth_service.login(request.username, request.password)
        if token is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"token": token}

    @app.get("/api/auth/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, str]:
        return require_session(app.state.session_store, authorization)

    @app.get("/api/retrieval-policy")
    def get_retrieval_policy(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        require_session(app.state.session_store, authorization)
        policy = RetrievalPolicy.load(app.state.retrieval_policy_path)
        return {
            "type_weight": policy.type_weights,
            "product_weight": policy.product_weights,
            "priority_boost": policy.priority_boosts,
            "formula": {
                "similarity_ratio": policy.similarity_ratio,
                "type_ratio": policy.type_ratio,
                "product_ratio": policy.product_ratio,
                "priority_ratio": policy.priority_ratio,
            },
            "top_k": {
                "initial": policy.top_k.initial,
                "after_rerank": policy.top_k.after_rerank,
                "final": policy.top_k.final,
            },
        }

    @app.post("/api/kb")
    def create_knowledge_base(request: KnowledgeBaseCreate) -> dict[str, Any]:
        if app.state.service_mode == "database":
            owner_id = app.state.default_owner_id
            if owner_id is None:
                raise HTTPException(status_code=500, detail="Database mode requires default_owner_id")
            item = app.state.kb_service.create(
                name=request.name,
                owner_id=owner_id,
                description=request.description,
                visibility=request.visibility,
            )
            return {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "visibility": item.visibility,
                "doc_count": item.doc_count,
            }

        return app.state.kb_service.create(
            name=request.name,
            description=request.description,
            visibility=request.visibility,
            owner_id="admin",
        )

    @app.get("/api/kb")
    def list_knowledge_bases(authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
        session = require_session(app.state.session_store, authorization)
        user_id = resolve_session_user_id(app, session)
        items = app.state.kb_service.list_for_user(user_id)
        if app.state.service_mode == "database":
            return {
                "items": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "description": item.description,
                        "visibility": item.visibility,
                        "doc_count": item.doc_count,
                    }
                    for item in items
                ]
            }
        return {"items": items}

    @app.get("/api/kb/{kb_id}")
    def get_knowledge_base(kb_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, lookup_id, resolve_session_user_id(app, session), "can_view")
        item = app.state.kb_service.get(lookup_id)
        if not item:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        if app.state.service_mode == "database":
            return {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "visibility": item.visibility,
                "doc_count": item.doc_count,
            }
        return item

    @app.put("/api/kb/{kb_id}")
    def update_knowledge_base(kb_id: str, request: KnowledgeBaseUpdate) -> dict[str, Any]:
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        item = app.state.kb_service.update(
            lookup_id,
            name=request.name,
            description=request.description,
            visibility=request.visibility,
        )
        if not item:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        if app.state.service_mode == "database":
            return {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "visibility": item.visibility,
                "doc_count": item.doc_count,
            }
        return item

    @app.delete("/api/kb/{kb_id}")
    def delete_knowledge_base(kb_id: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, lookup_id, resolve_session_user_id(app, session), "can_grant")
        item = app.state.kb_service.delete(lookup_id)
        if not item:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        return {"deleted": True}

    @app.get("/api/kb/{kb_id}/permissions")
    def list_kb_permissions(kb_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, lookup_id, resolve_session_user_id(app, session), "can_grant")
        items = app.state.kb_service.list_permissions(lookup_id)
        if items is None:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        if app.state.service_mode == "database":
            return {
                "items": [
                    {
                        "user_id": str(item.user_id),
                        "username": item.user.username,
                        "can_view": item.can_view,
                        "can_upload": item.can_upload,
                        "can_delete": item.can_delete,
                        "can_grant": item.can_grant,
                    }
                    for item in items
                ]
            }
        return {"items": items}

    @app.put("/api/kb/{kb_id}/permissions/{user_id}")
    def set_kb_permission(
        kb_id: str,
        user_id: str,
        request: KnowledgeBasePermissionUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, lookup_id, resolve_session_user_id(app, session), "can_grant")
        lookup_user_id = int(user_id) if app.state.service_mode == "database" else user_id
        item = app.state.kb_service.set_permission(
            lookup_id,
            lookup_user_id,
            {
                "can_view": request.can_view,
                "can_upload": request.can_upload,
                "can_delete": request.can_delete,
                "can_grant": request.can_grant,
            },
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        if app.state.service_mode == "database":
            return {
                "user_id": str(item.user_id),
                "username": item.user.username,
                "can_view": item.can_view,
                "can_upload": item.can_upload,
                "can_delete": item.can_delete,
                "can_grant": item.can_grant,
            }
        return item

    @app.delete("/api/kb/{kb_id}/permissions/{user_id}")
    def delete_kb_permission(
        kb_id: str,
        user_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, lookup_id, resolve_session_user_id(app, session), "can_grant")
        lookup_user_id = int(user_id) if app.state.service_mode == "database" else user_id
        item = app.state.kb_service.delete_permission(lookup_id, lookup_user_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        return {"deleted": True}

    @app.get("/api/kb/{kb_id}/view-rules/{user_id}")
    def get_knowledge_view_rule(
        kb_id: str,
        user_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, lookup_id, resolve_session_user_id(app, session), "can_grant")
        if app.state.service_mode != "database":
            raise HTTPException(status_code=501, detail="Knowledge view rules require database mode")
        lookup_user_id = int(user_id)
        rule = app.state.view_rule_service.get_rule(int(kb_id), lookup_user_id)
        if rule is None:
            return {
                "kb_id": int(kb_id),
                "user_id": lookup_user_id,
                "rule": None,
                "effective_scope": "all_documents",
            }
        return {**app.state.view_rule_service.serialize_rule(rule), "effective_scope": "restricted"}

    @app.put("/api/kb/{kb_id}/view-rules/{user_id}")
    def set_knowledge_view_rule(
        kb_id: str,
        user_id: str,
        request: KnowledgeViewRuleUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        current_user_id = resolve_session_user_id(app, session)
        require_kb_permission(app, lookup_id, current_user_id, "can_grant")
        if app.state.service_mode != "database":
            raise HTTPException(status_code=501, detail="Knowledge view rules require database mode")
        lookup_user_id = int(user_id)
        if not app.state.kb_service.has_permission(int(kb_id), lookup_user_id, "can_view"):
            raise HTTPException(status_code=422, detail="Target user requires can_view permission")
        rule = app.state.view_rule_service.set_rule(
            kb_id=int(kb_id),
            user_id=lookup_user_id,
            allowed_departments=request.allowed_departments,
            allowed_product_lines=request.allowed_product_lines,
            allowed_visibilities=request.allowed_visibilities,
            max_security_level=request.max_security_level,
        )
        return {**app.state.view_rule_service.serialize_rule(rule), "effective_scope": "restricted"}

    @app.delete("/api/kb/{kb_id}/view-rules/{user_id}")
    def delete_knowledge_view_rule(
        kb_id: str,
        user_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        lookup_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, lookup_id, resolve_session_user_id(app, session), "can_grant")
        if app.state.service_mode != "database":
            raise HTTPException(status_code=501, detail="Knowledge view rules require database mode")
        deleted = app.state.view_rule_service.delete_rule(int(kb_id), int(user_id))
        return {"deleted": deleted is not None, "effective_scope": "all_documents"}

    @app.post("/api/kb/{kb_id}/documents/upload")
    async def upload_document(
        kb_id: str,
        file: UploadFile,
        department: str = Form(default=""),
        product_line: str = Form(default=""),
        visibility: str = Form(default="internal"),
        security_level: int = Form(default=1),
        tags: str = Form(default=""),
        scope: str = Form(default="I"),
        document_type: str = Form(default="OTH"),
        product: str = Form(default="GEN"),
        priority: str = Form(default="P2"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        service_kb_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, service_kb_id, resolve_session_user_id(app, session), "can_upload")
        content = await file.read()
        allowed_scopes = {"C", "I", "R"}
        allowed_types = {"WP", "PI", "UM", "DG", "SOL", "SM", "SLA", "IG", "OM", "FT", "SPEC", "QG", "FL", "CP", "OTH", "CERT", "IMG"}
        allowed_products = {"MC", "MS", "MD", "MNO", "PRO", "UC", "LOC", "GEN"}
        allowed_priorities = {"P0", "P1", "P2"}
        if scope not in allowed_scopes or document_type not in allowed_types or product not in allowed_products or priority not in allowed_priorities:
            raise HTTPException(status_code=422, detail="Invalid document metadata")
        if app.state.service_mode == "database":
            storage = LocalFileStorageService(app.state.file_storage_root)
            stored = storage.save(
                content=content,
                original_filename=file.filename or "uploaded",
                content_type=file.content_type or "application/octet-stream",
                kb_id=service_kb_id,
            )
            try:
                doc = app.state.document_service.upload(
                    kb_id=service_kb_id,
                    filename=stored.original_filename,
                    content=content,
                    department=department,
                    product_line=product_line,
                    visibility=visibility,
                    security_level=security_level,
                    tags=tags,
                    scope=scope,
                    document_type=document_type,
                    product=product,
                    priority=priority,
                    storage_key=stored.storage_key,
                    original_filename=stored.original_filename,
                    content_type=stored.content_type,
                    file_size=stored.file_size,
                )
                ingestion = IngestionService(session=app.state.db_session, file_storage=storage)
                staged = ingestion.ingest_uploaded_document(document=doc)
                app.state.db_session.commit()
            except Exception:
                app.state.db_session.rollback()
                storage.delete(stored.storage_key)
                raise
            return {
                "id": doc.id,
                "kb_id": doc.kb_id,
                "title": doc.title,
                "file_type": doc.file_type,
                "status": doc.status,
                "department": doc.department,
                "product_line": doc.product_line,
                "visibility": doc.visibility,
                "security_level": doc.security_level,
                "tags": doc.tags,
                "scope": doc.scope,
                "document_type": doc.document_type,
                "product": doc.product,
                "priority": doc.priority,
                "storage_key": doc.storage_key,
                "original_filename": doc.original_filename,
                "content_type": doc.content_type,
                "file_size": doc.file_size,
                "download_available": storage.exists(doc.storage_key),
                "parse_task_id": staged["task_id"],
                "staged_filename": staged["staged_filename"],
                "block_count": staged["block_count"],
                "chunk_count": staged["chunk_count"],
            }

        doc = app.state.document_service.upload(
            kb_id=service_kb_id,
            filename=file.filename or "uploaded",
            content=content,
            department=department,
            product_line=product_line,
            visibility=visibility,
            security_level=security_level,
            tags=tags,
            scope=scope,
            document_type=document_type,
            product=product,
            priority=priority,
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        return doc

    @app.get("/api/kb/{kb_id}/documents")
    def list_documents(kb_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
        session = require_session(app.state.session_store, authorization)
        service_kb_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        user_id = resolve_session_user_id(app, session)
        require_kb_permission(app, service_kb_id, user_id, "can_view")
        items = app.state.document_service.list(service_kb_id)
        if items is None:
            raise HTTPException(status_code=404, detail="Knowledge base not found")
        if app.state.service_mode == "database":
            database_user_id = int(user_id)
            document_filter = app.state.document_filter_service.build_filter(
                kb_id=service_kb_id,
                user_id=database_user_id,
                user_roles=app.state.document_filter_service.roles_for_user(database_user_id),
            )
            items = [item for item in items if document_filter.matches(item)]
            return {
                "items": [
                    {
                        "id": item.id,
                        "kb_id": item.kb_id,
                        "title": item.title,
                        "file_type": item.file_type,
                        "status": item.status,
                        "scope": item.scope,
                        "document_type": item.document_type,
                        "product": item.product,
                        "priority": item.priority,
                    }
                    for item in items
                ]
            }
        return {"items": items}

    @app.get("/api/kb/{kb_id}/documents/{doc_id}")
    def get_document_detail(kb_id: str, doc_id: str, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        service_kb_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        user_id = resolve_session_user_id(app, session)
        require_kb_permission(app, service_kb_id, user_id, "can_view")
        service_doc_id = int(doc_id) if app.state.service_mode == "database" else doc_id
        item = app.state.document_service.get(service_kb_id, service_doc_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Document not found")
        if app.state.service_mode == "database":
            database_user_id = int(user_id)
            document_filter = app.state.document_filter_service.build_filter(
                kb_id=service_kb_id,
                user_id=database_user_id,
                user_roles=app.state.document_filter_service.roles_for_user(database_user_id),
            )
            if not document_filter.matches(item):
                raise HTTPException(status_code=403, detail="Permission denied")
            parse_task = (
                app.state.db_session.query(ParseTask)
                .filter(ParseTask.document_id == item.id, ParseTask.kb_id == item.kb_id)
                .order_by(ParseTask.id.desc())
                .first()
            )
            block_count = 0
            if parse_task is not None:
                block_count = (
                    app.state.db_session.query(ContentBlock)
                    .filter(ContentBlock.parse_task_id == parse_task.id)
                    .count()
                )
            chunk_count = (
                app.state.db_session.query(DocumentChunk)
                .filter(DocumentChunk.document_id == item.id)
                .count()
            )
            return {
                "id": item.id,
                "kb_id": item.kb_id,
                "title": item.title,
                "status": item.status,
                "file_type": item.file_type,
                "department": item.department,
                "product_line": item.product_line,
                "visibility": item.visibility,
                "security_level": item.security_level,
                "tags": item.tags,
                "scope": item.scope,
                "document_type": item.document_type,
                "product": item.product,
                "priority": item.priority,
                "block_count": block_count,
                "chunk_count": chunk_count,
            }
        return {
            "id": item["id"],
            "kb_id": item["kb_id"],
            "title": item["title"],
            "status": item["status"],
            "file_type": item["file_type"],
            "department": item.get("department", ""),
            "product_line": item.get("product_line", ""),
            "visibility": item.get("visibility", "internal"),
            "security_level": item.get("security_level", 1),
            "tags": item.get("tags", ""),
            "scope": item.get("scope", "I"),
            "document_type": item.get("document_type", "OTH"),
            "product": item.get("product", "GEN"),
            "priority": item.get("priority", "P2"),
            "block_count": 0,
            "chunk_count": 0,
        }

    @app.delete("/api/kb/{kb_id}/documents/{doc_id}")
    def delete_document(
        kb_id: str,
        doc_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        session = require_session(app.state.session_store, authorization)
        service_kb_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, service_kb_id, resolve_session_user_id(app, session), "can_delete")
        service_doc_id = int(doc_id) if app.state.service_mode == "database" else doc_id
        deleted = app.state.document_service.delete(service_kb_id, service_doc_id)
        if deleted is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"deleted": True}

    @app.get("/api/review")
    def list_review_queue() -> dict[str, list[dict[str, Any]]]:
        return {"items": app.state.review_queue}

    @app.get("/api/conflicts")
    def list_conflicts() -> dict[str, list[dict[str, Any]]]:
        return {"items": app.state.conflict_log}

    @app.get("/api/dashboard/summary")
    def dashboard_summary() -> dict[str, int]:
        return {
            "pending_review": len([item for item in app.state.review_queue if item.get("status", "pending") == "pending"]),
            "pending_conflicts": len([item for item in app.state.conflict_log if item.get("status", "pending") == "pending"]),
            "pending_tasks": len([item for item in app.state.parse_tasks if item.get("status", "pending") == "pending"]),
            "open_issues": len([item for item in app.state.knowledge_issues if item.get("status", "open") == "open"]),
        }

    @app.get("/api/issues")
    def list_knowledge_issues(
        kb_id: str | None = None,
        status: str | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, list[dict[str, Any]]]:
        if app.state.service_mode != "database":
            return {"items": app.state.knowledge_issues}
        if kb_id is None:
            raise HTTPException(status_code=422, detail="kb_id is required")
        session = require_session(app.state.session_store, authorization)
        user_id = resolve_session_user_id(app, session)
        require_kb_permission(app, int(kb_id), int(user_id), "can_grant")
        items = app.state.qa_ops_service.list_issues(kb_id=int(kb_id), status=status)
        return {
            "items": [
                {
                    "id": item.id,
                    "kb_id": item.kb_id,
                    "message_id": item.message_id,
                    "question": item.question,
                    "reason": item.reason,
                    "feedback_text": item.feedback_text,
                    "status": item.status,
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ]
        }

    @app.put("/api/issues/{issue_id}")
    def update_knowledge_issue(
        issue_id: str,
        request: KnowledgeIssueUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if app.state.service_mode != "database":
            raise HTTPException(status_code=404, detail="Knowledge issue not found")
        session = require_session(app.state.session_store, authorization)
        user_id = resolve_session_user_id(app, session)
        issue = app.state.qa_ops_service.get_issue(int(issue_id))
        if issue is None:
            raise HTTPException(status_code=404, detail="Knowledge issue not found")
        require_kb_permission(app, int(issue.kb_id), int(user_id), "can_grant")
        try:
            updated = app.state.qa_ops_service.update_issue_status(int(issue_id), request.status)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid issue status")
        return {"id": updated.id, "status": updated.status}

    @app.get("/api/qa/conversations")
    def list_qa_conversations(kb_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
        session = require_session(app.state.session_store, authorization)
        user_id = resolve_session_user_id(app, session)
        service_kb_id = int(kb_id) if app.state.service_mode == "database" else kb_id
        require_kb_permission(app, service_kb_id, user_id, "can_view")
        if app.state.service_mode != "database":
            return {"items": []}
        items = app.state.qa_ops_service.list_conversations(int(kb_id), int(user_id))
        return {
            "items": [
                {
                    "id": item.id,
                    "kb_id": item.kb_id,
                    "title": item.title,
                    "created_at": item.created_at.isoformat(),
                    "updated_at": item.updated_at.isoformat(),
                }
                for item in items
            ]
        }

    @app.get("/api/qa/conversations/{conversation_id}/messages")
    def list_qa_messages(conversation_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
        session = require_session(app.state.session_store, authorization)
        user_id = resolve_session_user_id(app, session)
        if app.state.service_mode != "database":
            return {"items": []}
        conversation = app.state.qa_ops_service.get_conversation(int(conversation_id))
        if conversation is None or conversation.user_id != int(user_id):
            raise HTTPException(status_code=404, detail="Conversation not found")
        require_kb_permission(app, conversation.kb_id, int(user_id), "can_view")
        items = app.state.qa_ops_service.list_messages(int(conversation_id), int(user_id))
        if items is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {
            "items": [
                {
                    "id": item.id,
                    "question": item.question,
                    "answer": item.answer,
                    "sources": json.loads(item.sources or "[]"),
                    "created_at": item.created_at.isoformat(),
                }
                for item in items
            ]
        }

    @app.post("/api/qa/feedback")
    def save_qa_feedback(request: QaFeedbackRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        user_id = resolve_session_user_id(app, session)
        if app.state.service_mode != "database":
            return {"saved": True, "issue_id": None}
        message = app.state.qa_ops_service.get_message(request.message_id)
        if message is None or message.user_id != int(user_id):
            raise HTTPException(status_code=404, detail="Message not found")
        require_kb_permission(app, message.kb_id, int(user_id), "can_view")
        _, issue = app.state.qa_ops_service.save_feedback(
            message_id=request.message_id,
            user_id=int(user_id),
            is_helpful=request.is_helpful,
            feedback_text=request.feedback_text,
        )
        return {"saved": True, "issue_id": issue.id if issue is not None else None}

    @app.post("/api/qa/ask/sync")
    async def ask_sync(request: AskRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        user_id = resolve_session_user_id(app, session)
        service_kb_id = int(request.kb_id) if app.state.service_mode == "database" else request.kb_id
        require_kb_permission(app, service_kb_id, user_id, "can_view")
        if app.state.service_mode == "database":
            database_user_id = int(user_id)
            document_filter = app.state.document_filter_service.build_filter(
                kb_id=int(request.kb_id),
                user_id=database_user_id,
                user_roles=app.state.document_filter_service.roles_for_user(database_user_id),
            )
            chunks = DbChunkLoader(app.state.db_session).load_chunks(
                kb_id=int(request.kb_id),
                document_filter=document_filter,
            )
            retrieval_kb_id = f"{request.kb_id}:user:{database_user_id}"
            app.state.rag_service.tools.vector_chunks_by_kb[retrieval_kb_id] = chunks
            app.state.rag_service.tools.build_bm25_index(retrieval_kb_id, chunks)
        else:
            retrieval_kb_id = request.kb_id
        result = await app.state.rag_service.ask(
            question=request.question,
            kb_id=retrieval_kb_id,
            conversation_id=request.conversation_id,
        )
        if app.state.service_mode == "database":
            conversation, message = app.state.qa_ops_service.record_answer(
                kb_id=int(request.kb_id),
                user_id=int(user_id),
                question=request.question,
                answer=result["answer"],
                sources=result.get("sources", []),
                conversation_id=int(request.conversation_id) if request.conversation_id else None,
            )
            return {**result, "conversation_id": conversation.id, "message_id": message.id}
        return result


def create_app_from_env(env: Mapping[str, str]) -> FastAPI:
    database_url = env.get("DATABASE_URL")
    if database_url:
        session_factory = create_session_factory(database_url)
        app = create_app(mode="database", session_factory=session_factory)
        default_owner_id = env.get("DEFAULT_OWNER_ID")
        if default_owner_id:
            app.state.default_owner_id = int(default_owner_id)
        return app
    return create_app()


app = create_app_from_env(__import__("os").environ)
