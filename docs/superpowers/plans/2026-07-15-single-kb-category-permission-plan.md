# 单知识库分类权限模型实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将当前“资料分类 = 多个知识库”的实现调整为“一个企业 AI 知识库 + 多资料分类 + 分类级权限”，保证文档列表、下载、问答检索和管理授权都按资料分类隔离。

**架构：** 保留 `knowledge_bases` 作为企业级知识库容器，新增 `document_categories` 和 `document_category_permissions` 承载资料分类与分类权限。`documents` 增加 `category_id`，访问控制优先检查分类权限；KB 权限保留为系统管理员/企业知识库管理入口，不再表达业务资料权限。

**技术栈：** FastAPI、SQLAlchemy ORM、SQLite runtime schema migration、pytest、原生 HTML/CSS/JS。

---

## 文件结构

### 新增文件

- `backend/app/models/category.py`
  - 定义 `DocumentCategory`、`DocumentCategoryPermission` 两个 ORM 模型。
- `backend/app/services/category_service.py`
  - 分类 CRUD、分类权限增删改查、用户可访问分类计算。
- `backend/tests/test_category_permissions.py`
  - 分类模型、分类权限、分类访问过滤的单元测试。
- `backend/tests/test_single_kb_acceptance.py`
  - 单知识库验收结构测试：启动脚本创建一个 KB、六个分类、分类权限矩阵。

### 修改文件

- `backend/app/models/__init__.py`
  - 导出 `DocumentCategory`、`DocumentCategoryPermission`。
- `backend/app/models/knowledge_base.py`
  - `KnowledgeBase` 增加 `categories` relationship。
- `backend/app/models/document.py`
  - `Document` 增加 `category_id` 外键与 `category` relationship。
- `backend/app/core/db.py`
  - runtime migration 增加分类表、分类权限表、`documents.category_id` 列；为历史数据回填默认分类。
- `backend/app/main.py`
  - 注册 `category_service`；新增分类 API；文档上传、列表、详情、下载、删除、QA 按分类权限检查。
- `backend/app/services/document_filter_service.py`
  - `EffectiveDocumentFilter` 增加 `allowed_category_ids`，过滤文档分类。
- `backend/app/services/document_access_service.py`
  - 按分类权限过滤文档列表/详情/下载。
- `backend/app/services/db_document_service.py`
  - 上传时写入 `category_id`，列表支持按分类过滤。
- `backend/app/services/db_chunk_loader.py`
  - 加载 chunk 时携带 `category_id`，并用分类权限过滤。
- `backend/app/static/documents.html`
  - 左侧从“知识库列表”调整为“资料分类列表”。
- `backend/app/static/documents.js`
  - 加载一个企业 KB 下的分类，按分类列文档；授权面板改为分类权限。
- `backend/app/static/admin.html`
  - 管理页从多 KB 管理调整为单 KB + 分类管理。
- `backend/app/static/admin.js`
  - 上传表单增加 `category_id`；权限编辑改为分类权限。
- `backend/app/static/qa.html`
  - 问答页保留一个 KB，增加“全部有权限分类 / 指定分类”选择。
- `backend/app/static/qa.js`
  - QA 请求传 `category_id` 可选参数；默认在所有有权限分类中检索。
- `start_acceptance.sh`
  - 重写验收数据初始化：只创建一个 `企业 AI 知识库`，六个 `DocumentCategory`，导入文档时写 `category_id`，权限写入 `DocumentCategoryPermission`。
- `docs/implementation/permission-matrix-acceptance.md`
  - 更新验收说明，从“多个知识库”改为“一个知识库多分类权限”。

---

## 任务 1：新增分类 ORM 模型与导出

**文件：**
- 创建：`backend/app/models/category.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/app/models/knowledge_base.py`
- 修改：`backend/app/models/document.py`
- 测试：`backend/tests/test_category_permissions.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/tests/test_category_permissions.py` 添加：

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import Document, DocumentCategory, DocumentCategoryPermission, KnowledgeBase, Role, User


def test_document_belongs_to_category_and_category_belongs_to_single_kb():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="系统管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="企业 AI 知识库", description="", visibility="department", owner=user)
    category = DocumentCategory(kb=kb, name="产品报价配置表", description="报价资料", sort_order=40)
    document = Document(
        kb=kb,
        category=category,
        title="报价.xlsx",
        file_type="xlsx",
        status="stored_unsupported",
    )
    session.add_all([role, user, kb, category, document])
    session.commit()

    stored = session.query(Document).one()
    assert stored.category.name == "产品报价配置表"
    assert stored.category.kb.name == "企业 AI 知识库"


def test_category_permission_records_download_and_admin_flags():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="普通用户", level=1)
    user = User(username="sales_cn", password_hash="hash", role=role)
    owner = User(username="admin", password_hash="hash", role=Role(name="系统管理员", level=3))
    kb = KnowledgeBase(name="企业 AI 知识库", description="", visibility="department", owner=owner)
    category = DocumentCategory(kb=kb, name="产品报价配置表", sort_order=40)
    permission = DocumentCategoryPermission(
        category=category,
        user=user,
        can_view=True,
        can_download=True,
        can_upload=False,
        can_delete=False,
        can_grant=False,
    )
    session.add_all([role, owner.role, user, owner, kb, category, permission])
    session.commit()

    stored = session.query(DocumentCategoryPermission).one()
    assert stored.category.name == "产品报价配置表"
    assert stored.user.username == "sales_cn"
    assert stored.can_view is True
    assert stored.can_download is True
    assert stored.can_upload is False
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
pytest backend/tests/test_category_permissions.py -q
```

预期：失败，错误包含 `ImportError` 或 `cannot import name 'DocumentCategory'`。

- [ ] **步骤 3：创建分类模型**

创建 `backend/app/models/category.py`：

```python
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class DocumentCategory(Base):
    __tablename__ = "document_categories"
    __table_args__ = (UniqueConstraint("kb_id", "name", name="uq_document_categories_kb_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    kb: Mapped["KnowledgeBase"] = relationship(back_populates="categories")
    documents: Mapped[list["Document"]] = relationship(back_populates="category")
    permissions: Mapped[list["DocumentCategoryPermission"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
    )


class DocumentCategoryPermission(Base):
    __tablename__ = "document_category_permissions"
    __table_args__ = (UniqueConstraint("category_id", "user_id", name="uq_category_permissions_category_user"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("document_categories.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    can_view: Mapped[bool] = mapped_column(Boolean, default=False)
    can_download: Mapped[bool] = mapped_column(Boolean, default=False)
    can_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    can_grant: Mapped[bool] = mapped_column(Boolean, default=False)

    category: Mapped[DocumentCategory] = relationship(back_populates="permissions")
    user: Mapped["User"] = relationship()
```

- [ ] **步骤 4：修改模型关系与导出**

在 `backend/app/models/knowledge_base.py` 的 `KnowledgeBase` 中增加：

```python
    categories: Mapped[list["DocumentCategory"]] = relationship(back_populates="kb")
```

在 `backend/app/models/document.py` 中：

```python
    category_id: Mapped[int | None] = mapped_column(ForeignKey("document_categories.id"), nullable=True, index=True)
```

并在 relationship 区域增加：

```python
    category: Mapped["DocumentCategory | None"] = relationship(back_populates="documents")
```

在 `backend/app/models/__init__.py` 增加导入：

```python
from app.models.category import DocumentCategory, DocumentCategoryPermission
```

并加入 `__all__`。

- [ ] **步骤 5：运行测试验证通过**

运行：

```bash
pytest backend/tests/test_category_permissions.py -q
```

预期：`2 passed`。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/models backend/tests/test_category_permissions.py
git commit -m "feat: add document category permission models"
```

---

## 任务 2：新增分类服务

**文件：**
- 创建：`backend/app/services/category_service.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_category_permissions.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/tests/test_category_permissions.py` 追加：

```python
from app.services.category_service import CategoryService


def test_category_service_lists_only_viewable_categories_for_user():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    admin_role = Role(name="系统管理员", level=3)
    normal_role = Role(name="普通用户", level=1)
    admin = User(username="admin", password_hash="hash", role=admin_role)
    sales = User(username="sales_cn", password_hash="hash", role=normal_role)
    finance = User(username="finance_user", password_hash="hash", role=normal_role)
    kb = KnowledgeBase(name="企业 AI 知识库", description="", visibility="department", owner=admin)
    public_category = DocumentCategory(kb=kb, name="POC产品资料", sort_order=10)
    price_category = DocumentCategory(kb=kb, name="产品报价配置表", sort_order=40)
    session.add_all([admin_role, normal_role, admin, sales, finance, kb, public_category, price_category])
    session.flush()

    service = CategoryService(session)
    service.set_permission(public_category.id, sales.id, {"can_view": True, "can_download": True, "can_upload": False, "can_delete": False, "can_grant": False})
    service.set_permission(price_category.id, sales.id, {"can_view": True, "can_download": True, "can_upload": False, "can_delete": False, "can_grant": False})
    service.set_permission(public_category.id, finance.id, {"can_view": True, "can_download": True, "can_upload": False, "can_delete": False, "can_grant": False})

    assert [item.name for item in service.list_for_user(kb.id, sales.id)] == ["POC产品资料", "产品报价配置表"]
    assert [item.name for item in service.list_for_user(kb.id, finance.id)] == ["POC产品资料"]
    assert service.has_permission(price_category.id, finance.id, "can_view") is False
    assert service.has_permission(price_category.id, sales.id, "can_download") is True
```

- [ ] **步骤 2：运行测试验证失败**

```bash
pytest backend/tests/test_category_permissions.py::test_category_service_lists_only_viewable_categories_for_user -q
```

预期：失败，错误包含 `No module named 'app.services.category_service'`。

- [ ] **步骤 3：实现分类服务**

创建 `backend/app/services/category_service.py`：

```python
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import DocumentCategory, DocumentCategoryPermission


class CategoryService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, kb_id: int, name: str, description: str = "", sort_order: int = 0) -> DocumentCategory:
        category = DocumentCategory(kb_id=kb_id, name=name, description=description, sort_order=sort_order)
        self.session.add(category)
        self.session.commit()
        self.session.refresh(category)
        return category

    def get(self, category_id: int) -> DocumentCategory | None:
        return self.session.get(DocumentCategory, category_id)

    def list_by_kb(self, kb_id: int) -> list[DocumentCategory]:
        return (
            self.session.query(DocumentCategory)
            .filter(DocumentCategory.kb_id == kb_id)
            .order_by(DocumentCategory.sort_order, DocumentCategory.id)
            .all()
        )

    def list_for_user(self, kb_id: int, user_id: int) -> list[DocumentCategory]:
        return (
            self.session.query(DocumentCategory)
            .join(DocumentCategoryPermission, DocumentCategoryPermission.category_id == DocumentCategory.id)
            .filter(
                DocumentCategory.kb_id == kb_id,
                DocumentCategoryPermission.user_id == user_id,
                DocumentCategoryPermission.can_view.is_(True),
            )
            .order_by(DocumentCategory.sort_order, DocumentCategory.id)
            .all()
        )

    def list_permissions(self, category_id: int) -> list[DocumentCategoryPermission]:
        return (
            self.session.query(DocumentCategoryPermission)
            .filter(DocumentCategoryPermission.category_id == category_id)
            .order_by(DocumentCategoryPermission.user_id)
            .all()
        )

    def set_permission(self, category_id: int, user_id: int, payload: dict[str, bool]) -> DocumentCategoryPermission | None:
        if self.get(category_id) is None:
            return None
        permission = (
            self.session.query(DocumentCategoryPermission)
            .filter(
                DocumentCategoryPermission.category_id == category_id,
                DocumentCategoryPermission.user_id == user_id,
            )
            .one_or_none()
        )
        if permission is None:
            permission = DocumentCategoryPermission(category_id=category_id, user_id=user_id)
            self.session.add(permission)
        permission.can_view = payload["can_view"]
        permission.can_download = payload["can_download"]
        permission.can_upload = payload["can_upload"]
        permission.can_delete = payload["can_delete"]
        permission.can_grant = payload["can_grant"]
        self.session.commit()
        self.session.refresh(permission)
        return permission

    def delete_permission(self, category_id: int, user_id: int) -> DocumentCategoryPermission | None:
        permission = (
            self.session.query(DocumentCategoryPermission)
            .filter(
                DocumentCategoryPermission.category_id == category_id,
                DocumentCategoryPermission.user_id == user_id,
            )
            .one_or_none()
        )
        if permission is None:
            return None
        self.session.delete(permission)
        self.session.commit()
        return permission

    def has_permission(self, category_id: int, user_id: int, permission: str) -> bool:
        record = (
            self.session.query(DocumentCategoryPermission)
            .filter(
                DocumentCategoryPermission.category_id == category_id,
                DocumentCategoryPermission.user_id == user_id,
            )
            .one_or_none()
        )
        if record is None:
            return False
        return bool(getattr(record, permission, False))

    def permitted_category_ids(self, kb_id: int, user_id: int, permission: str = "can_view") -> set[int]:
        return {
            category_id
            for category_id, in self.session.query(DocumentCategory.id)
            .join(DocumentCategoryPermission, DocumentCategoryPermission.category_id == DocumentCategory.id)
            .filter(
                DocumentCategory.kb_id == kb_id,
                DocumentCategoryPermission.user_id == user_id,
                getattr(DocumentCategoryPermission, permission).is_(True),
            )
            .all()
        }
```

- [ ] **步骤 4：注册服务到 app state**

在 `backend/app/main.py` 的 `build_app_state_services()` database 分支返回值中增加：

```python
"category_service": CategoryService(session),
```

在 memory 分支增加：

```python
"category_service": None,
```

在 `create_app()` 中增加：

```python
app.state.category_service = services["category_service"]
```

并在 import 区增加：

```python
from app.services.category_service import CategoryService
```

- [ ] **步骤 5：运行测试验证通过**

```bash
pytest backend/tests/test_category_permissions.py::test_category_service_lists_only_viewable_categories_for_user -q
```

预期：`1 passed`。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/services/category_service.py backend/app/main.py backend/tests/test_category_permissions.py
git commit -m "feat: add category permission service"
```

---

## 任务 3：runtime schema 支持分类表和 documents.category_id

**文件：**
- 修改：`backend/app/core/db.py`
- 测试：`backend/tests/test_runtime_database_mode.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/tests/test_runtime_database_mode.py` 追加：

```python
def test_runtime_schema_adds_categories_and_backfills_existing_documents():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE roles (id INTEGER PRIMARY KEY, name VARCHAR(50), level INTEGER)
            """))
            connection.execute(text("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50), password_hash VARCHAR(255), role_id INTEGER)
            """))
            connection.execute(text("""
                CREATE TABLE knowledge_bases (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(128),
                    description TEXT,
                    visibility VARCHAR(16),
                    doc_count INTEGER,
                    owner_id INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    kb_id INTEGER,
                    title VARCHAR(512),
                    file_type VARCHAR(16),
                    status VARCHAR(16),
                    department VARCHAR(100),
                    product_line VARCHAR(100),
                    visibility VARCHAR(30),
                    security_level INTEGER,
                    tags TEXT,
                    scope VARCHAR(1),
                    document_type VARCHAR(16),
                    product VARCHAR(16),
                    priority VARCHAR(2),
                    acl_roles TEXT,
                    storage_key VARCHAR(1024),
                    original_filename VARCHAR(512),
                    content_type VARCHAR(255),
                    file_size INTEGER
                )
            """))
            connection.execute(text("""
                INSERT INTO roles (id, name, level) VALUES (1, '系统管理员', 3)
            """))
            connection.execute(text("""
                INSERT INTO users (id, username, password_hash, role_id) VALUES (1, 'admin', 'hash', 1)
            """))
            connection.execute(text("""
                INSERT INTO knowledge_bases (id, name, description, visibility, doc_count, owner_id)
                VALUES (1, '企业 AI 知识库', '', 'department', 1, 1)
            """))
            connection.execute(text("""
                INSERT INTO documents (id, kb_id, title, file_type, status, tags)
                VALUES (1, 1, '报价.xlsx', 'xlsx', 'stored_unsupported', '产品报价配置表')
            """))

        create_app_from_env({"DATABASE_URL": f"sqlite:///{db_path}"})

        inspector = inspect(engine)
        assert "document_categories" in inspector.get_table_names()
        assert "document_category_permissions" in inspector.get_table_names()
        assert "category_id" in {column["name"] for column in inspector.get_columns("documents")}
        rows = engine.connect().execute(text("""
            SELECT d.title, c.name
            FROM documents d
            JOIN document_categories c ON c.id = d.category_id
        """)).all()
        assert rows == [("报价.xlsx", "产品报价配置表")]
    finally:
        os.remove(db_path)
```

- [ ] **步骤 2：运行测试验证失败**

```bash
pytest backend/tests/test_runtime_database_mode.py::test_runtime_schema_adds_categories_and_backfills_existing_documents -q
```

预期：失败，错误显示缺少 `document_categories` 表或 `documents.category_id` 列。

- [ ] **步骤 3：实现 runtime schema migration**

在 `backend/app/core/db.py` 增加常量：

```python
_DEFAULT_CATEGORIES = [
    ("POC产品资料", 10),
    ("MCX产品资料", 20),
    ("定位产品资料", 30),
    ("产品报价配置表", 40),
    ("产品规划文档", 50),
    ("客服问答资料库", 60),
]

_CATEGORY_KEYWORDS = [
    ("定位产品资料", ["定位"]),
    ("客服问答资料库", ["故障", "运维", "SLA", "服务等级"]),
    ("产品报价配置表", ["销售", "话术", "报价"]),
    ("MCX产品资料", ["MCX"]),
    ("产品规划文档", ["白皮书", "产品介绍", "解决方案", "功能清单", "认证", "快速指南"]),
    ("POC产品资料", ["MCSTARS", "MiniServer", "POC"]),
]
```

在 `ensure_runtime_schema(engine)` 中，在 column migration 后加入：

```python
        table_names = set(inspector.get_table_names())
        if "document_categories" not in table_names:
            connection.execute(text("""
                CREATE TABLE document_categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kb_id INTEGER NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY(kb_id) REFERENCES knowledge_bases(id),
                    CONSTRAINT uq_document_categories_kb_name UNIQUE (kb_id, name)
                )
            """))
        if "document_category_permissions" not in table_names:
            connection.execute(text("""
                CREATE TABLE document_category_permissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    can_view BOOLEAN NOT NULL DEFAULT 0,
                    can_download BOOLEAN NOT NULL DEFAULT 0,
                    can_upload BOOLEAN NOT NULL DEFAULT 0,
                    can_delete BOOLEAN NOT NULL DEFAULT 0,
                    can_grant BOOLEAN NOT NULL DEFAULT 0,
                    FOREIGN KEY(category_id) REFERENCES document_categories(id),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    CONSTRAINT uq_category_permissions_category_user UNIQUE (category_id, user_id)
                )
            """))
```

把 `_DOCUMENT_COLUMNS` 增加：

```python
"category_id": "INTEGER",
```

在新增列后执行默认分类 seed 与回填：

```python
        for name, sort_order in _DEFAULT_CATEGORIES:
            connection.execute(text("""
                INSERT OR IGNORE INTO document_categories (kb_id, name, description, sort_order)
                SELECT id, :name, :description, :sort_order FROM knowledge_bases
            """), {"name": name, "description": f"默认资料分类：{name}", "sort_order": sort_order})

        category_case = " ".join(
            f"WHEN title LIKE '%{keyword}%' OR tags LIKE '%{keyword}%' THEN '{category}'"
            for category, keywords in _CATEGORY_KEYWORDS
            for keyword in keywords
        )
        connection.execute(text(f"""
            UPDATE documents
            SET category_id = (
                SELECT c.id
                FROM document_categories c
                WHERE c.kb_id = documents.kb_id
                  AND c.name = CASE {category_case} ELSE 'POC产品资料' END
                LIMIT 1
            )
            WHERE category_id IS NULL
        """))
```

- [ ] **步骤 4：运行测试验证通过**

```bash
pytest backend/tests/test_runtime_database_mode.py::test_runtime_schema_adds_categories_and_backfills_existing_documents -q
```

预期：`1 passed`。

- [ ] **步骤 5：运行 runtime schema 相关测试**

```bash
pytest backend/tests/test_runtime_database_mode.py -q
```

预期：全部通过。

- [ ] **步骤 6：Commit**

```bash
git add backend/app/core/db.py backend/tests/test_runtime_database_mode.py
git commit -m "feat: migrate runtime schema for document categories"
```

---

## 任务 4：分类 API 与权限 API

**文件：**
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_category_permissions.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/tests/test_category_permissions.py` 追加 FastAPI 测试：

```python
from fastapi.testclient import TestClient
from app.main import create_app
from app.services.password_service import hash_password


def test_category_api_lists_only_user_viewable_categories():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    admin_role = Role(name="系统管理员", level=3)
    normal_role = Role(name="普通用户", level=1)
    admin = User(username="admin", password_hash=hash_password("Demo12345"), role=admin_role)
    sales = User(username="sales_cn", password_hash=hash_password("Demo12345"), role=normal_role)
    kb = KnowledgeBase(name="企业 AI 知识库", description="", visibility="department", owner=admin)
    session.add_all([admin_role, normal_role, admin, sales, kb])
    session.flush()
    poc = DocumentCategory(kb=kb, name="POC产品资料", sort_order=10)
    price = DocumentCategory(kb=kb, name="产品报价配置表", sort_order=40)
    session.add_all([poc, price])
    session.flush()
    session.add_all([
        DocumentCategoryPermission(category=poc, user=sales, can_view=True, can_download=True),
        DocumentCategoryPermission(category=price, user=admin, can_view=True, can_download=True, can_upload=True, can_delete=True, can_grant=True),
    ])
    session.commit()

    app = create_app(mode="database", session=session)
    client = TestClient(app)
    token = client.post("/api/auth/login", json={"username": "sales_cn", "password": "Demo12345"}).json()["session_token"]

    response = client.get(f"/api/kb/{kb.id}/categories", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == ["POC产品资料"]
```

- [ ] **步骤 2：运行测试验证失败**

```bash
pytest backend/tests/test_category_permissions.py::test_category_api_lists_only_user_viewable_categories -q
```

预期：失败，HTTP `404 Not Found`。

- [ ] **步骤 3：增加请求模型和序列化函数**

在 `backend/app/main.py` 中增加：

```python
class CategoryCreate(BaseModel):
    name: str
    description: str = ""
    sort_order: int = 0


class CategoryPermissionUpdate(BaseModel):
    can_view: bool = False
    can_download: bool = False
    can_upload: bool = False
    can_delete: bool = False
    can_grant: bool = False


def serialize_category(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "kb_id": item.kb_id,
        "name": item.name,
        "description": item.description,
        "sort_order": item.sort_order,
    }
```

- [ ] **步骤 4：增加分类列表与创建 API**

在 KB routes 后增加：

```python
    @app.get("/api/kb/{kb_id}/categories")
    def list_categories(kb_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
        session = require_session(app.state.session_store, authorization)
        if app.state.service_mode != "database":
            raise HTTPException(status_code=501, detail="Categories require database mode")
        current_user_id = int(resolve_session_user_id(app, session))
        service_kb_id = int(kb_id)
        can_manage_kb = app.state.kb_service.has_permission(service_kb_id, current_user_id, "can_grant")
        items = (
            app.state.category_service.list_by_kb(service_kb_id)
            if can_manage_kb
            else app.state.category_service.list_for_user(service_kb_id, current_user_id)
        )
        return {"items": [serialize_category(item) for item in items]}

    @app.post("/api/kb/{kb_id}/categories")
    def create_category(kb_id: str, request: CategoryCreate, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        if app.state.service_mode != "database":
            raise HTTPException(status_code=501, detail="Categories require database mode")
        service_kb_id = int(kb_id)
        require_kb_permission(app, service_kb_id, resolve_session_user_id(app, session), "can_grant")
        category = app.state.category_service.create(
            kb_id=service_kb_id,
            name=request.name,
            description=request.description,
            sort_order=request.sort_order,
        )
        return serialize_category(category)
```

- [ ] **步骤 5：增加分类权限 API**

继续在 `backend/app/main.py` 增加：

```python
    @app.get("/api/categories/{category_id}/permissions")
    def list_category_permissions(category_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
        session = require_session(app.state.session_store, authorization)
        if app.state.service_mode != "database":
            raise HTTPException(status_code=501, detail="Category permissions require database mode")
        category = app.state.category_service.get(int(category_id))
        if category is None:
            raise HTTPException(status_code=404, detail="Category not found")
        if not app.state.category_service.has_permission(category.id, int(resolve_session_user_id(app, session)), "can_grant"):
            require_kb_permission(app, category.kb_id, resolve_session_user_id(app, session), "can_grant")
        items = app.state.category_service.list_permissions(category.id)
        return {
            "items": [
                {
                    "user_id": str(item.user_id),
                    "username": item.user.username,
                    "can_view": item.can_view,
                    "can_download": item.can_download,
                    "can_upload": item.can_upload,
                    "can_delete": item.can_delete,
                    "can_grant": item.can_grant,
                }
                for item in items
            ]
        }

    @app.put("/api/categories/{category_id}/permissions/{user_id}")
    def set_category_permission(
        category_id: str,
        user_id: str,
        request: CategoryPermissionUpdate,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        session = require_session(app.state.session_store, authorization)
        if app.state.service_mode != "database":
            raise HTTPException(status_code=501, detail="Category permissions require database mode")
        category = app.state.category_service.get(int(category_id))
        if category is None:
            raise HTTPException(status_code=404, detail="Category not found")
        if not app.state.category_service.has_permission(category.id, int(resolve_session_user_id(app, session)), "can_grant"):
            require_kb_permission(app, category.kb_id, resolve_session_user_id(app, session), "can_grant")
        item = app.state.category_service.set_permission(
            int(category_id),
            int(user_id),
            {
                "can_view": request.can_view,
                "can_download": request.can_download,
                "can_upload": request.can_upload,
                "can_delete": request.can_delete,
                "can_grant": request.can_grant,
            },
        )
        if item is None:
            raise HTTPException(status_code=404, detail="Category not found")
        return {
            "user_id": str(item.user_id),
            "username": item.user.username,
            "can_view": item.can_view,
            "can_download": item.can_download,
            "can_upload": item.can_upload,
            "can_delete": item.can_delete,
            "can_grant": item.can_grant,
        }
```

- [ ] **步骤 6：运行测试验证通过**

```bash
pytest backend/tests/test_category_permissions.py::test_category_api_lists_only_user_viewable_categories -q
```

预期：`1 passed`。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/main.py backend/tests/test_category_permissions.py
git commit -m "feat: expose document category APIs"
```

---

## 任务 5：文档上传、列表、详情、下载按分类权限控制

**文件：**
- 修改：`backend/app/services/db_document_service.py`
- 修改：`backend/app/services/document_filter_service.py`
- 修改：`backend/app/services/document_access_service.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_category_permissions.py`

- [ ] **步骤 1：编写失败测试**

追加测试：

```python
def test_document_list_and_download_respect_category_permission(tmp_path):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    admin_role = Role(name="系统管理员", level=3)
    normal_role = Role(name="普通用户", level=1)
    admin = User(username="admin", password_hash=hash_password("Demo12345"), role=admin_role)
    finance = User(username="finance_user", password_hash=hash_password("Demo12345"), role=normal_role)
    kb = KnowledgeBase(name="企业 AI 知识库", description="", visibility="department", owner=admin)
    public_category = DocumentCategory(kb=kb, name="POC产品资料", sort_order=10)
    price_category = DocumentCategory(kb=kb, name="产品报价配置表", sort_order=40)
    session.add_all([admin_role, normal_role, admin, finance, kb, public_category, price_category])
    session.flush()
    session.add(DocumentCategoryPermission(category=public_category, user=finance, can_view=True, can_download=True))
    session.add_all([
        Document(kb=kb, category=public_category, title="公开.docx", file_type="docx", status="parsed", storage_key="public.docx", original_filename="公开.docx"),
        Document(kb=kb, category=price_category, title="报价.xlsx", file_type="xlsx", status="stored_unsupported", storage_key="price.xlsx", original_filename="报价.xlsx"),
    ])
    session.commit()

    app = create_app(mode="database", session=session)
    app.state.file_storage_root = tmp_path
    (tmp_path / "public.docx").write_bytes(b"public")
    (tmp_path / "price.xlsx").write_bytes(b"price")
    client = TestClient(app)
    token = client.post("/api/auth/login", json={"username": "finance_user", "password": "Demo12345"}).json()["session_token"]

    listed = client.get(f"/api/kb/{kb.id}/documents", headers={"Authorization": f"Bearer {token}"})
    assert listed.status_code == 200
    assert [item["title"] for item in listed.json()["items"]] == ["公开.docx"]

    forbidden = client.get(f"/api/kb/{kb.id}/documents/2/download", headers={"Authorization": f"Bearer {token}"})
    assert forbidden.status_code == 403
```

- [ ] **步骤 2：运行测试验证失败**

```bash
pytest backend/tests/test_category_permissions.py::test_document_list_and_download_respect_category_permission -q
```

预期：失败，当前仍可能返回两个文档或下载返回 200。

- [ ] **步骤 3：扩展 DbDocumentService**

在 `backend/app/services/db_document_service.py`：

```python
    def upload(..., category_id: int | None = None, ...) -> Document:
```

创建 Document 时增加：

```python
            category_id=category_id,
```

把 `list()` 改为支持分类过滤：

```python
    def list(self, kb_id: int, category_ids: set[int] | None = None) -> list[Document]:
        query = self.session.query(Document).filter(Document.kb_id == kb_id)
        if category_ids is not None:
            query = query.filter(Document.category_id.in_(category_ids))
        return query.order_by(Document.id).all()
```

- [ ] **步骤 4：扩展文档过滤**

在 `EffectiveDocumentFilter` 增加字段：

```python
    allowed_category_ids: set[int] | None
```

在 `matches()` 开头增加：

```python
        if self.allowed_category_ids is not None and document.category_id not in self.allowed_category_ids:
            return False
```

所有构造 `EffectiveDocumentFilter(...)` 的位置都显式传入 `allowed_category_ids`。

- [ ] **步骤 5：修改文档列表接口**

在 `backend/app/main.py` 的 `list_documents()` database 分支中改成：

```python
            permitted_category_ids = app.state.category_service.permitted_category_ids(
                int(lookup_id),
                int(user_id),
                "can_view",
            )
            items = app.state.document_service.list(lookup_id, permitted_category_ids)
```

返回序列化时确保包含：

```python
"category_id": item.category_id,
"category_name": item.category.name if item.category else "",
```

- [ ] **步骤 6：修改下载接口**

在 `download_document()` database 分支获取 document 后增加：

```python
            if document.category_id is None or not app.state.category_service.has_permission(
                document.category_id,
                int(user_id),
                "can_download",
            ):
                raise HTTPException(status_code=403, detail="Permission denied")
```

- [ ] **步骤 7：修改上传接口**

给 `upload_document()` 增加表单参数：

```python
        category_id: int = Form(...),
```

在 database 分支校验：

```python
            category = app.state.category_service.get(category_id)
            if category is None or category.kb_id != service_kb_id:
                raise HTTPException(status_code=422, detail="Invalid category")
            if not app.state.category_service.has_permission(category_id, int(resolve_session_user_id(app, session)), "can_upload"):
                raise HTTPException(status_code=403, detail="Permission denied")
```

调用 `document_service.upload()` 时传：

```python
category_id=category_id,
```

- [ ] **步骤 8：运行测试验证通过**

```bash
pytest backend/tests/test_category_permissions.py::test_document_list_and_download_respect_category_permission -q
```

预期：`1 passed`。

- [ ] **步骤 9：Commit**

```bash
git add backend/app/main.py backend/app/services/db_document_service.py backend/app/services/document_filter_service.py backend/app/services/document_access_service.py backend/tests/test_category_permissions.py
git commit -m "feat: enforce category permissions for documents"
```

---

## 任务 6：问答检索按分类权限过滤

**文件：**
- 修改：`backend/app/services/db_chunk_loader.py`
- 修改：`backend/app/main.py`
- 修改：`backend/app/services/rag_service.py`
- 测试：`backend/tests/test_category_permissions.py`

- [ ] **步骤 1：编写失败测试**

追加测试：

```python
def test_qa_retrieval_only_loads_chunks_from_permitted_categories():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    admin_role = Role(name="系统管理员", level=3)
    normal_role = Role(name="普通用户", level=1)
    admin = User(username="admin", password_hash="hash", role=admin_role)
    finance = User(username="finance_user", password_hash="hash", role=normal_role)
    kb = KnowledgeBase(name="企业 AI 知识库", description="", visibility="department", owner=admin)
    public_category = DocumentCategory(kb=kb, name="POC产品资料", sort_order=10)
    price_category = DocumentCategory(kb=kb, name="产品报价配置表", sort_order=40)
    session.add_all([admin_role, normal_role, admin, finance, kb, public_category, price_category])
    session.flush()
    session.add(DocumentCategoryPermission(category=public_category, user=finance, can_view=True, can_download=True))
    public_doc = Document(kb=kb, category=public_category, title="公开.docx", file_type="docx", status="parsed")
    price_doc = Document(kb=kb, category=price_category, title="报价.docx", file_type="docx", status="parsed")
    session.add_all([public_doc, price_doc])
    session.flush()
    session.add_all([
        DocumentChunk(document=public_doc, chunk_index=0, content="公开资料内容"),
        DocumentChunk(document=price_doc, chunk_index=0, content="报价机密内容"),
    ])
    session.commit()

    from app.services.db_chunk_loader import DbChunkLoader
    from app.services.document_filter_service import EffectiveDocumentFilter

    loader = DbChunkLoader(session)
    document_filter = EffectiveDocumentFilter(
        allow_all=True,
        allowed_category_ids={public_category.id},
        allowed_scopes=None,
        allowed_departments=None,
        allowed_products=None,
        allowed_roles=None,
        max_security_level=None,
    )

    chunks = loader.load_chunks(kb.id, document_filter=document_filter)

    assert [chunk["content"] for chunk in chunks] == ["公开资料内容"]
    assert chunks[0]["category_id"] == public_category.id
```

- [ ] **步骤 2：运行测试验证失败**

```bash
pytest backend/tests/test_category_permissions.py::test_qa_retrieval_only_loads_chunks_from_permitted_categories -q
```

预期：失败，因为 `EffectiveDocumentFilter` 尚无 `allowed_category_ids` 或 chunk 未包含 `category_id`。

- [ ] **步骤 3：修改 DbChunkLoader**

在 `_serialize_chunk()` metadata 中增加：

```python
"category_id": chunk.document.category_id,
"category_name": chunk.document.category.name if chunk.document.category else "",
```

- [ ] **步骤 4：修改 QA request 模型**

在 `QaRequest` 增加：

```python
    category_id: int | None = None
```

- [ ] **步骤 5：修改 QA ask 接口**

在 `/api/qa/ask/sync` database 分支构建过滤器时：

```python
            permitted_category_ids = app.state.category_service.permitted_category_ids(
                int(request.kb_id),
                int(user_id),
                "can_view",
            )
            if request.category_id is not None:
                if request.category_id not in permitted_category_ids:
                    raise HTTPException(status_code=403, detail="Permission denied")
                permitted_category_ids = {request.category_id}
            document_filter = app.state.document_filter_service.build_filter(
                int(request.kb_id),
                int(user_id),
                app.state.document_filter_service.roles_for_user(int(user_id)),
            )
            document_filter = replace(document_filter, allowed_category_ids=permitted_category_ids)
```

在 import 区增加：

```python
from dataclasses import replace
```

- [ ] **步骤 6：运行测试验证通过**

```bash
pytest backend/tests/test_category_permissions.py::test_qa_retrieval_only_loads_chunks_from_permitted_categories -q
```

预期：`1 passed`。

- [ ] **步骤 7：Commit**

```bash
git add backend/app/main.py backend/app/services/db_chunk_loader.py backend/app/services/rag_service.py backend/tests/test_category_permissions.py
git commit -m "feat: filter qa retrieval by category permissions"
```

---

## 任务 7：验收脚本改为一个 KB + 六个分类

**文件：**
- 修改：`start_acceptance.sh`
- 测试：`backend/tests/test_single_kb_acceptance.py`

- [ ] **步骤 1：编写失败测试**

创建 `backend/tests/test_single_kb_acceptance.py`：

```python
from pathlib import Path


def test_acceptance_script_uses_single_kb_with_categories():
    script = Path("start_acceptance.sh").read_text(encoding="utf-8")

    assert "KnowledgeBase(" in script
    assert "企业 AI 知识库" in script
    assert "DocumentCategory(" in script
    assert "DocumentCategoryPermission(" in script
    assert "kbs = {}" not in script
    assert "kb = kbs[category]" not in script
```

- [ ] **步骤 2：运行测试验证失败**

```bash
pytest backend/tests/test_single_kb_acceptance.py -q
```

预期：失败，因为脚本仍然使用 `kbs = {}` 和多个 `KnowledgeBase`。

- [ ] **步骤 3：修改脚本 import**

在 `start_acceptance.sh` Python import 模型处加入：

```python
    DocumentCategory,
    DocumentCategoryPermission,
```

删除对多 KB 权限作为分类权限的依赖。

- [ ] **步骤 4：创建单一 KB 和分类**

替换当前 `kbs = {}` 和循环创建多个 KB 的代码为：

```python
kb = KnowledgeBase(
    name="企业 AI 知识库",
    description="企业统一资料库，按资料分类控制访问、下载与编辑权限。",
    visibility="department",
    doc_count=0,
    owner_id=users["admin"].id,
)
session.add(kb)
session.flush()

categories = {}
for index, category_name in enumerate(CATEGORIES, start=1):
    category = DocumentCategory(
        kb_id=kb.id,
        name=category_name,
        description=f"甲方资料分类：{category_name}",
        sort_order=index * 10,
    )
    session.add(category)
    session.flush()
    categories[category_name] = category
```

- [ ] **步骤 5：写入分类权限**

新增函数：

```python
def grant_category(category: DocumentCategory, user: User, can_view: bool, can_download: bool = True, can_upload: bool = False, can_delete: bool = False, can_grant: bool = False) -> None:
    permission = session.query(DocumentCategoryPermission).filter_by(category_id=category.id, user_id=user.id).one_or_none()
    if permission is None:
        permission = DocumentCategoryPermission(category_id=category.id, user_id=user.id)
        session.add(permission)
    permission.can_view = can_view
    permission.can_download = can_download
    permission.can_upload = can_upload
    permission.can_delete = can_delete
    permission.can_grant = can_grant
```

保留 KB 级权限只给系统管理员：

```python
grant(kb, users["admin"], True, True, True, True)
```

分类权限：

```python
for category_name, category in categories.items():
    for username in CATEGORY_ADMINS[category_name]:
        grant_category(category, users[username], True, True, True, True, True)
    viewers = PRICE_VIEWERS if category_name == "产品报价配置表" else ALL_NORMAL_USERS
    for username in viewers:
        grant_category(category, users[username], True, True)
```

- [ ] **步骤 6：导入文档时写 category_id**

把：

```python
kb = kbs[category]
```

改为：

```python
category_item = categories[category]
```

保存文件仍按单 KB：

```python
stored = storage.save(content, filename, content_type_for(suffix), str(kb.id))
```

创建 Document 时增加：

```python
kb_id=kb.id,
category_id=category_item.id,
```

- [ ] **步骤 7：更新 doc_count**

替换多 KB doc_count 循环为：

```python
kb.doc_count = session.query(Document).filter_by(kb_id=kb.id).count()
```

- [ ] **步骤 8：运行测试验证通过**

```bash
pytest backend/tests/test_single_kb_acceptance.py -q
```

预期：`1 passed`。

- [ ] **步骤 9：Commit**

```bash
git add start_acceptance.sh backend/tests/test_single_kb_acceptance.py
git commit -m "feat: seed acceptance data as single categorized kb"
```

---

## 任务 8：前端改为单 KB + 分类导航

**文件：**
- 修改：`backend/app/static/documents.html`
- 修改：`backend/app/static/documents.js`
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/admin.js`
- 修改：`backend/app/static/qa.html`
- 修改：`backend/app/static/qa.js`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/tests/test_frontend_shell.py` 追加：

```python
def test_documents_shell_uses_single_kb_category_navigation():
    html = (STATIC_DIR / "documents.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "documents.js").read_text(encoding="utf-8")

    assert "资料分类" in html
    assert "documents-category-list" in html
    assert "/categories" in js
    assert "activeCategoryId" in js


def test_admin_shell_uploads_with_category_selector():
    html = (STATIC_DIR / "admin.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "admin.js").read_text(encoding="utf-8")

    assert "document-category" in html
    assert "资料分类" in html
    assert "category_id" in js


def test_qa_shell_supports_all_permitted_categories():
    html = (STATIC_DIR / "qa.html").read_text(encoding="utf-8")
    js = (STATIC_DIR / "qa.js").read_text(encoding="utf-8")

    assert "qa-category-select" in html
    assert "全部有权限分类" in html
    assert "category_id" in js
```

- [ ] **步骤 2：运行测试验证失败**

```bash
pytest backend/tests/test_frontend_shell.py::test_documents_shell_uses_single_kb_category_navigation backend/tests/test_frontend_shell.py::test_admin_shell_uploads_with_category_selector backend/tests/test_frontend_shell.py::test_qa_shell_supports_all_permitted_categories -q
```

预期：至少一个失败，因为前端仍围绕多 KB 列表。

- [ ] **步骤 3：修改 documents.html**

把左侧列表容器改为：

```html
<section id="documents-category-list" class="button-list"></section>
<p id="documents-category-empty-state" class="empty-state">暂无可访问资料分类。</p>
```

保留 `documents-kb-list` 可以隐藏或移除；如果保留，设置 `hidden`，避免破坏旧测试。

- [ ] **步骤 4：修改 documents.js 状态与加载**

增加：

```javascript
let activeCategoryId = null
let categories = []
```

新增：

```javascript
async function refreshCategories() {
  if (!activeKbId) return
  const body = await authorizedJson(`/api/kb/${activeKbId}/categories`)
  categories = body?.items || []
  if (!categories.some((item) => item.id === activeCategoryId)) {
    activeCategoryId = categories[0]?.id || null
  }
  renderCategories()
  await refreshKnowledgeBaseView()
}
```

文档过滤增加：

```javascript
(!activeCategoryId || documentItem.category_id === activeCategoryId)
```

- [ ] **步骤 5：修改 admin 上传表单**

在 `admin.html` 上传元数据区域增加：

```html
<label>资料分类 <select id="document-category" name="category_id"></select></label>
```

在 `admin.js` 上传 FormData 中追加：

```javascript
formData.append('category_id', document.getElementById('document-category').value)
```

加载 KB 后调用 `/api/kb/${activeKbId}/categories` 填充该 select。

- [ ] **步骤 6：修改 QA 分类选择**

在 `qa.html` KB selector 下增加：

```html
<label>资料分类
  <select id="qa-category-select">
    <option value="">全部有权限分类</option>
  </select>
</label>
```

在 `qa.js` 请求体中加入：

```javascript
const categoryId = document.getElementById('qa-category-select')?.value || ''
const payload = { kb_id: activeKbId, question }
if (categoryId) payload.category_id = Number(categoryId)
```

- [ ] **步骤 7：运行前端 shell 测试**

```bash
pytest backend/tests/test_frontend_shell.py -q
```

预期：全部通过。

- [ ] **步骤 8：Commit**

```bash
git add backend/app/static backend/tests/test_frontend_shell.py
git commit -m "feat: update frontend for categorized single kb"
```

---

## 任务 9：文档与验收说明更新

**文件：**
- 修改：`docs/implementation/permission-matrix-acceptance.md`
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`

- [ ] **步骤 1：更新权限矩阵说明**

在 `docs/implementation/permission-matrix-acceptance.md` 明确写入：

```markdown
# 权限矩阵验收说明

本系统一期采用“一个企业 AI 知识库 + 多资料分类 + 分类级权限”的模型。

- 知识库：企业 AI 知识库
- 资料分类：POC 产品资料、MCX 产品资料、定位产品资料、产品报价配置表、产品规划文档、客服问答资料库
- 权限挂载点：资料分类，而不是知识库本身，也不是单个文档
```

- [ ] **步骤 2：更新 API 文档**

在 `docs/api/api-reference.md` 增加：

```markdown
## 资料分类 API

- `GET /api/kb/{kb_id}/categories`：列出当前用户在该知识库下可访问的资料分类；具备 KB 授权权限的管理员可看到全部分类。
- `POST /api/kb/{kb_id}/categories`：创建资料分类，需要 KB 授权权限。
- `GET /api/categories/{category_id}/permissions`：列出分类权限，需要分类授权权限或 KB 授权权限。
- `PUT /api/categories/{category_id}/permissions/{user_id}`：设置分类权限。
```

- [ ] **步骤 3：更新代码映射文档**

在 `docs/implementation/tech-code-mapping.md` 增加分类模型映射：

```markdown
| 能力 | 代码位置 |
| --- | --- |
| 资料分类模型 | `backend/app/models/category.py` |
| 分类权限服务 | `backend/app/services/category_service.py` |
| 分类权限 API | `backend/app/main.py` |
| 分类过滤检索 | `backend/app/services/document_filter_service.py`, `backend/app/services/db_chunk_loader.py` |
```

- [ ] **步骤 4：运行文档相关静态测试**

```bash
pytest backend/tests/test_frontend_shell.py -q
```

预期：全部通过。文档本身无专用测试，用前端 shell 测试确认静态资源仍完整。

- [ ] **步骤 5：Commit**

```bash
git add docs/implementation/permission-matrix-acceptance.md docs/api/api-reference.md docs/implementation/tech-code-mapping.md
git commit -m "docs: document categorized single kb permission model"
```

---

## 任务 10：端到端回归验证

**文件：**
- 修改：无
- 测试：全量测试与验收脚本

- [ ] **步骤 1：运行分类权限测试**

```bash
pytest backend/tests/test_category_permissions.py backend/tests/test_single_kb_acceptance.py -q
```

预期：全部通过。

- [ ] **步骤 2：运行后端全量测试**

```bash
pytest backend/tests -q
```

预期：全部通过。

- [ ] **步骤 3：运行一键验收脚本启动服务**

```bash
./start_acceptance.sh
```

预期输出包含：

```text
验收账号
admin / Demo12345
```

服务启动在 `http://127.0.0.1:8000` 或脚本输出的 host/port。

- [ ] **步骤 4：手工验收分类权限**

使用浏览器验证：

```text
admin：能看到企业 AI 知识库、六个资料分类、所有文档、分类授权入口。
sales_cn：能看到产品报价配置表。
finance_user：不能看到产品报价配置表，不能下载报价类文档。
delivery_user：能下载普通产品资料，不能管理分类权限。
```

- [ ] **步骤 5：手工验收问答权限**

在 QA 页面验证：

```text
sales_cn：选择“全部有权限分类”提问，可以返回有权限分类来源。
finance_user：针对报价类问题不应返回产品报价配置表来源。
```

- [ ] **步骤 6：记录验证结果**

在终端记录实际命令输出；如果有失败，不要声称完成，回到对应任务修复。

- [ ] **步骤 7：Commit 验证文档或最终整理**

如果新增验收记录文件：

```bash
git add docs/implementation
 git commit -m "test: verify categorized single kb acceptance flow"
```

---

## 自检结果

- 规格覆盖度：覆盖了单 KB、六分类、分类权限、文档上传/下载/列表、QA 检索、前端导航、验收脚本和文档更新。
- 占位符扫描：未保留“待定 / TODO / 后续实现”等占位内容。
- 类型一致性：统一使用 `DocumentCategory`、`DocumentCategoryPermission`、`category_id`、`can_download`、`CategoryService`。
- 范围控制：本计划只处理“一个知识库多分类权限”的主线调整，不引入文档级权限或复杂规则引擎。
