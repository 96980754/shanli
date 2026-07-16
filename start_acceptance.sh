#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
DB_PATH="$BACKEND_DIR/acceptance.db"
UPLOAD_ROOT="$BACKEND_DIR/acceptance_uploads"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

cd "$BACKEND_DIR"
rm -f "$DB_PATH"
rm -rf "$UPLOAD_ROOT"

python3 - <<'PY'
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, ensure_runtime_schema
from app.models import (
    ContentBlock,
    Document,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeBasePermission,
    ParseTask,
    Role,
    User,
)
from app.services.file_storage import LocalFileStorageService
from app.services.ingestion_service import IngestionService
from app.services.password_service import hash_password

backend_dir = Path.cwd()
root_dir = backend_dir.parent
db_path = backend_dir / "acceptance.db"
upload_root = backend_dir / "acceptance_uploads"
sample_zip = root_dir / "data" / "examples" / "AI知识库_数据样本包.zip"

if not sample_zip.exists():
    raise SystemExit(f"样本包不存在：{sample_zip}")

engine = create_engine(f"sqlite:///{db_path}")
Base.metadata.create_all(engine)
ensure_runtime_schema(engine)
Session = sessionmaker(bind=engine)
session = Session()
storage = LocalFileStorageService(upload_root)

PASSWORD = "Demo12345"
CATEGORIES = [
    "POC产品资料",
    "MCX产品资料",
    "定位产品资料",
    "产品报价配置表",
    "产品规划文档",
    "客服问答资料库",
]
CATEGORY_RULES = [
    ("定位产品资料", ["定位"]),
    ("客服问答资料库", ["故障", "运维", "SLA", "服务等级"]),
    ("产品报价配置表", ["销售", "话术"]),
    ("MCX产品资料", ["MCX"]),
    ("产品规划文档", ["白皮书", "产品介绍", "解决方案", "功能清单", "认证证书", "快速指南"]),
    ("POC产品资料", ["MCSTARS", "MiniServer"]),
]
USER_DEFINITIONS = [
    ("admin", "系统管理员"),
    ("kb_poc_admin", "知识库管理员"),
    ("price_admin", "文档管理员"),
    ("product_manager", "知识库管理员"),
    ("support_manager", "知识库管理员"),
    ("sales_cn", "普通用户"),
    ("sales_intl", "普通用户"),
    ("marketing_support", "普通用户"),
    ("product_user", "普通用户"),
    ("ops_user", "普通用户"),
    ("support_user", "普通用户"),
    ("delivery_user", "普通用户"),
    ("finance_user", "其它"),
]
ALL_NORMAL_USERS = [
    "sales_cn",
    "sales_intl",
    "marketing_support",
    "product_user",
    "ops_user",
    "support_user",
    "delivery_user",
]
PRICE_VIEWERS = ["sales_cn", "sales_intl", "marketing_support", "product_user", "ops_user"]
CATEGORY_ADMINS = {
    "POC产品资料": ["kb_poc_admin", "admin"],
    "MCX产品资料": ["kb_poc_admin", "admin"],
    "定位产品资料": ["kb_poc_admin", "admin"],
    "产品报价配置表": ["price_admin", "admin"],
    "产品规划文档": ["product_manager", "admin"],
    "客服问答资料库": ["support_manager", "admin"],
}

PRODUCT_BY_CATEGORY = {
    "POC产品资料": "MC",
    "MCX产品资料": "MC",
    "定位产品资料": "LOC",
    "产品报价配置表": "MC",
    "产品规划文档": "GEN",
    "客服问答资料库": "MC",
}
DOCUMENT_TYPE_KEYWORDS = [
    ("白皮书", "WP"),
    ("产品介绍", "PI"),
    ("用户手册", "UM"),
    ("安装手册", "IG"),
    ("部署指南", "DG"),
    ("解决方案", "SOL"),
    ("销售", "SM"),
    ("SLA", "SLA"),
    ("运维", "OM"),
    ("故障", "FT"),
    ("快速指南", "QG"),
    ("功能清单", "FL"),
    ("竞品", "CP"),
    ("认证", "CERT"),
]


def get_role(name: str) -> Role:
    role = session.query(Role).filter_by(name=name).one_or_none()
    if role is None:
        level = 3 if name in {"系统管理员", "知识库管理员"} else 2 if name == "文档管理员" else 1
        role = Role(name=name, level=level)
        session.add(role)
        session.flush()
    return role


def get_user(username: str, role_name: str) -> User:
    user = session.query(User).filter_by(username=username).one_or_none()
    if user is None:
        user = User(username=username, password_hash=hash_password(PASSWORD), role=get_role(role_name))
        session.add(user)
    else:
        user.password_hash = hash_password(PASSWORD)
        user.role = get_role(role_name)
    session.flush()
    return user


def category_for(filename: str) -> str:
    for category, keywords in CATEGORY_RULES:
        if any(keyword.lower() in filename.lower() for keyword in keywords):
            return category
    return "POC产品资料"


def document_type_for(filename: str) -> str:
    for keyword, code in DOCUMENT_TYPE_KEYWORDS:
        if keyword.lower() in filename.lower():
            return code
    return "OTH"


def content_type_for(suffix: str) -> str:
    return {
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(suffix, "application/octet-stream")


def grant(kb: KnowledgeBase, user: User, can_view: bool, can_upload: bool = False, can_delete: bool = False, can_grant: bool = False) -> None:
    permission = session.query(KnowledgeBasePermission).filter_by(kb_id=kb.id, user_id=user.id).one_or_none()
    if permission is None:
        permission = KnowledgeBasePermission(kb_id=kb.id, user_id=user.id)
        session.add(permission)
    permission.can_view = can_view
    permission.can_upload = can_upload
    permission.can_delete = can_delete
    permission.can_grant = can_grant


users = {username: get_user(username, role_name) for username, role_name in USER_DEFINITIONS}
kbs = {}
for category in CATEGORIES:
    kb = KnowledgeBase(
        name=category,
        description=f"甲方分类级权限验收资料库：{category}",
        visibility="department",
        doc_count=0,
        owner_id=users["admin"].id,
    )
    session.add(kb)
    session.flush()
    kbs[category] = kb

for category, kb in kbs.items():
    for username in CATEGORY_ADMINS[category]:
        grant(kb, users[username], True, True, True, True)
    viewers = PRICE_VIEWERS if category == "产品报价配置表" else ALL_NORMAL_USERS
    for username in viewers:
        grant(kb, users[username], True)

imported = []
with ZipFile(sample_zip) as archive:
    for info in archive.infolist():
        if info.is_dir():
            continue
        filename = Path(info.filename).name
        suffix = Path(filename).suffix.lower()
        if suffix not in {".docx", ".pdf", ".pptx", ".xlsx"}:
            continue
        category = category_for(filename)
        kb = kbs[category]
        content = archive.read(info.filename)
        stored = storage.save(content, filename, content_type_for(suffix), str(kb.id))
        document = Document(
            kb_id=kb.id,
            title=filename,
            file_type=suffix.lstrip("."),
            status="pending",
            department="产品管理部" if category in {"产品规划文档", "产品报价配置表"} else "售后" if category == "客服问答资料库" else "营销运作部",
            product_line="定位" if category == "定位产品资料" else "MCX" if category == "MCX产品资料" else "MCSTARS",
            visibility="internal",
            security_level=2 if category == "产品报价配置表" else 1,
            tags=category,
            scope="I",
            document_type=document_type_for(filename),
            product=PRODUCT_BY_CATEGORY[category],
            priority="P1",
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            content_type=stored.content_type,
            file_size=stored.file_size,
            acl_roles=json.dumps([], ensure_ascii=False),
        )
        session.add(document)
        session.flush()
        if suffix in {".pptx", ".xlsx"}:
            document.status = "stored_unsupported"
        else:
            try:
                result = IngestionService(session, storage).ingest_uploaded_document(document)
                if result.get("chunk_count", 0) == 0:
                    document.status = "stored_unsupported"
            except Exception:
                document.status = "stored_unsupported"
        imported.append((category, filename, document.status))

for kb in kbs.values():
    kb.doc_count = session.query(Document).filter_by(kb_id=kb.id).count()

session.commit()

print(users["admin"].id)
print("\n真实样本分类级验收数据已准备：")
for category in CATEGORIES:
    kb = kbs[category]
    print(f"- {category}: {kb.doc_count} 份文件")
print("\n账号密码：")
for username, role_name in USER_DEFINITIONS:
    print(f"- {username} / {PASSWORD}（{role_name}）")
print("\n导入文件：")
for category, filename, status in imported:
    print(f"- [{category}] {filename} -> {status}")
PY

DEFAULT_OWNER_ID="$(python3 - <<'PY'
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import User
engine = create_engine('sqlite:///./acceptance.db')
Session = sessionmaker(bind=engine)
session = Session()
print(session.query(User).filter_by(username='admin').one().id)
PY
)"

cat <<EOF

验收服务即将启动：
- 地址：http://localhost:${PORT}/login
- 管理员：admin / Demo12345
- 普通用户示例：sales_cn / Demo12345
- 无权限反向验证：finance_user / Demo12345
- 数据库：${DB_PATH}
- 文件目录：${UPLOAD_ROOT}

如需停止服务，请在本终端按 Ctrl+C。
EOF

DATABASE_URL="sqlite:///./acceptance.db" \
DEFAULT_OWNER_ID="$DEFAULT_OWNER_ID" \
FILE_STORAGE_ROOT="$UPLOAD_ROOT" \
python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT"
