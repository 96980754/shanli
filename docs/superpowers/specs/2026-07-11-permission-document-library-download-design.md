# 权限文档库访问与下载设计

**日期：** 2026-07-11

**目标：** 在现有知识库权限、知识视图规则和文档元数据基础上，实现用户按权限浏览、查看和下载文档的能力；通过可替换的文件存储抽象，一期先使用本地存储，后续可平滑切换到 MinIO。

---

## 1. 设计范围

本阶段包含：

1. 文档列表按用户权限和知识视图规则过滤；
2. 文档详情按同一规则授权；
3. 原文件下载 API；
4. 本地文件存储抽象；
5. 新上传文档保存稳定存储元数据；
6. 下载成功写入审计日志；
7. 新增 `/documents` 用户文档库页面；
8. 管理员在拥有 `can_grant` 时可绕过知识视图规则；
9. 测试、API 文档和技术映射同步。

本阶段不包含：

- MinIO 实现；
- 文档在线预览；
- 文档搜索、标签筛选和分页；
- 文档级编辑权限；
- `can_download` 独立权限；
- 历史文档原文件回填；
- RAG 检索过滤接入；
- 图谱子图过滤接入。

---

## 2. 访问规则

### 2.1 总规则

一期采用：

> **有权查看某篇文档，即有权下载该文档原文件。**

不新增 `can_download`。

### 2.2 文档访问判断顺序

```text
用户是否拥有知识库 can_view？
├─ 否 → 拒绝 403
└─ 是
   ├─ 是否拥有 can_grant？
   │  └─ 是 → 允许访问知识库全部文档
   └─ 否
      ├─ 是否存在 KnowledgeViewRule？
      │  └─ 否 → 允许访问知识库全部文档
      └─ 是 → 文档必须符合规则的元数据范围
               ├─ 符合 → 允许
               └─ 不符合 → 拒绝 403
```

### 2.3 列表、详情与下载的差异

| 操作 | 文档不存在 | 无 `can_view` / 不符合规则 | 有权限但原文件不存在 |
|---|---|---|---|
| 文档列表 | 不适用 | 不展示该文档 | 仍展示，标记不可下载 |
| 文档详情 | `404 Document not found` | `403 Permission denied` | 正常返回详情，`download_available=false` |
| 文件下载 | `404 Document not found` | `403 Permission denied` | `404 File not found` |

列表隐藏无权文档，但详情和下载明确返回 403，满足当前已确认的权限反馈策略。

### 2.4 分级权限兼容原则

本阶段的 `DocumentAccessService` 不应把权限来源写死为“仅用户级规则”。它应消费最终生效的知识视图：

```text
effective_view_rule
```

当前阶段的 `effective_view_rule` 直接等于用户在知识库下的 `KnowledgeViewRule`；后续新增系统默认、角色/等级、部门/项目规则时，按以下优先级计算有效规则：

```text
用户知识库单独规则
> 项目 / 部门规则
> 角色 / 等级规则
> 系统默认规则
```

文档访问、下载、后续 RAG 和图谱过滤只依赖有效规则，不依赖规则来源。这样后续权限分级不会推翻本阶段的存储、下载和访问 API。

---

## 3. 统一文档访问服务

新增：

```text
backend/app/services/document_access_service.py
```

职责：让文档列表、详情、下载和后续 RAG 使用同一套授权判断，避免每个 API 复制权限逻辑。

接口：

```python
class DocumentAccessService:
    def can_access_document(
        self,
        kb_id: int,
        user_id: int,
        document: Document,
    ) -> bool:
        ...

    def filter_accessible_documents(
        self,
        kb_id: int,
        user_id: int,
        documents: list[Document],
    ) -> list[Document]:
        ...
```

实现逻辑：

```python
if not kb_service.has_permission(kb_id, user_id, "can_view"):
    return False

if kb_service.has_permission(kb_id, user_id, "can_grant"):
    return True

rule = view_rule_service.get_rule(kb_id, user_id)
return view_rule_service.can_view_document(document, rule)
```

内存模式在当前阶段保持已有知识库级行为；知识视图规则只在数据库模式生效。

---

## 4. 文件存储抽象

### 4.1 接口

新增：

```text
backend/app/services/file_storage.py
```

定义：

```python
@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    original_filename: str
    content_type: str
    file_size: int


class FileStorageService(Protocol):
    def save(
        self,
        content: bytes,
        original_filename: str,
        content_type: str,
        kb_id: int,
    ) -> StoredFile:
        ...

    def open(self, storage_key: str) -> BinaryIO:
        ...

    def exists(self, storage_key: str) -> bool:
        ...

    def delete(self, storage_key: str) -> None:
        ...
```

### 4.2 一期实现：本地存储

新增：

```text
LocalFileStorageService
```

文件根目录由：

```text
app.state.file_storage_root
```

提供，默认：

```text
uploads/files
```

目录结构：

```text
uploads/files/
└── knowledge-bases/
    └── {kb_id}/
        └── documents/
            └── {uuid}-{safe_filename}
```

数据库只记录相对 `storage_key`：

```text
knowledge-bases/101/documents/uuid-P368-manual.pdf
```

禁止绝对路径、`..` 路径穿越和直接暴露服务器路径。

### 4.3 后续 MinIO 替换

后续新增 `MinioFileStorageService`，保持业务 API、`storage_key` 和 `Document` 存储元数据不变。

下载可从后端流式返回演进为：

```text
应用鉴权 → 生成 1-5 分钟短时签名 URL
```

---

## 5. 文档模型扩展

`Document` 新增字段：

| 字段 | 类型 | 默认 | 用途 |
|---|---|---|---|
| `storage_key` | string | `""` | 存储系统内部文件标识 |
| `original_filename` | string | `""` | 原始上传文件名 |
| `content_type` | string | `""` | MIME 类型 |
| `file_size` | integer | `0` | 原文件字节数 |

现有文档元数据保留：

- `department`
- `product_line`
- `visibility`
- `security_level`
- `tags`

### 5.1 历史文档策略

历史文档若没有 `storage_key`：

- 仍可用于详情和后续检索；
- `download_available=false`；
- 下载返回：

```json
{
  "detail": "File not found"
}
```

本阶段不尝试从旧解析目录推断文件路径，也不做原文件补传。

---

## 6. 上传链路

新上传链路：

```text
UploadFile
→ LocalFileStorageService.save()
→ StoredFile
→ Document 写 storage_key/original_filename/content_type/file_size
→ IngestionService 从已保存原文件解析
→ ContentBlock / DocumentChunk
```

`IngestionService` 不再承担“唯一文件保存职责”；其职责收敛为：

```text
读取已保存文件 → 创建 ParseTask → 解析 → 写 ContentBlock / DocumentChunk
```

上传 API 响应增加：

```json
{
  "storage_key": "knowledge-bases/101/documents/uuid-manual.pdf",
  "original_filename": "P368 用户手册.pdf",
  "content_type": "application/pdf",
  "file_size": 5242880,
  "download_available": true
}
```

不向用户返回服务器绝对文件路径。

---

## 7. API 设计

### 7.1 文档列表增强

```http
GET /api/kb/{kb_id}/documents
```

行为：

- 先验证知识库 `can_view`；
- `can_grant` 用户返回全部；
- 普通用户返回经过 `KnowledgeViewRule` 过滤的文档；
- 每项返回元数据和：

```json
{
  "download_available": true
}
```

### 7.2 文档详情增强

```http
GET /api/kb/{kb_id}/documents/{doc_id}
```

行为：

- 文档不存在：404；
- 文档存在但 `DocumentAccessService` 拒绝：403；
- 有权限：详情增加：

```json
{
  "original_filename": "P368 用户手册.pdf",
  "content_type": "application/pdf",
  "file_size": 5242880,
  "download_available": true
}
```

### 7.3 新增下载接口

```http
GET /api/kb/{kb_id}/documents/{doc_id}/download
```

处理：

```text
登录
→ 知识库定位
→ 文档定位
→ DocumentAccessService 权限判断
→ storage_key 存在性判断
→ 写审计日志
→ FileResponse 返回附件
```

成功响应头：

```text
Content-Disposition: attachment; filename*=UTF-8''...
Content-Type: 文档 MIME 类型
```

### 7.4 审计规则

下载成功后写：

```text
AuditLog.action = "download_document"
AuditLog.target_type = "document"
AuditLog.target_id = 文档 ID
AuditLog.kb_id = 知识库 ID
AuditLog.user_id = 当前用户
```

---

## 8. AuditLog 扩展

现有 `AuditLog` 扩展：

- `target_id: string`
- `kb_id: integer | null`
- `detail: text`
- `created_at: datetime`

本阶段只要求成功下载写审计。拒绝访问的安全审计后续另做。

---

## 9. `/documents` 用户文档库页面

### 9.1 页面与静态文件

新增：

```text
GET /documents
backend/app/static/documents.html
backend/app/static/documents.js
```

### 9.2 页面能力

- 显示当前用户；
- 加载当前用户可访问知识库；
- 选择知识库；
- 加载已过滤文档列表；
- 查看文档详情和元数据；
- 下载有权限且有原文件的文档；
- 从 `/qa`、`/admin` 互相跳转。

### 9.3 下载前端行为

因为 `Authorization: Bearer` 不能自动随普通 `<a>` 请求带上，下载使用：

```javascript
const response = await fetch(url, {
  headers: { Authorization: `Bearer ${token}` },
})
const blob = await response.blob()
const objectUrl = URL.createObjectURL(blob)
// 临时 a 元素触发下载
URL.revokeObjectURL(objectUrl)
```

前端根据 `download_available` 隐藏或禁用下载按钮。

---

## 10. 删除一致性

对于带有 `storage_key` 的新文档：

```text
验证 can_delete
→ 删除派生解析数据
→ 删除原始存储对象
→ 删除 Document
```

一期策略：

- 文件删除失败时，不删除数据库文档；
- 返回错误，避免产生无存储对象但数据库记录已删除的情况。

后续可演进为异步补偿任务。

---

## 11. 测试策略

### 文件存储

- 安全 storage_key；
- 保存、打开、存在、删除；
- 重名文件不覆盖；
- 路径穿越拒绝。

### 文档访问服务

- 无 `can_view` 拒绝；
- `can_grant` 绕过规则；
- 无规则允许；
- 有规则按元数据过滤；
- 列表过滤与详情判断一致。

### 下载 API

- 新上传文件有权下载成功；
- 下载响应内容和 content-disposition 正确；
- 下载成功写审计；
- 无 `can_view` 返回 403；
- 不符合规则返回 403；
- 历史文档无 storage_key 返回 404 File not found。

### 用户文档库页面

- `/documents` 返回 200；
- 页面包含知识库选择、文档列表、详情、下载节点；
- JS 包含文档加载和 fetch 下载钩子。

---

## 12. 验收标准

1. 新上传文档保存稳定 storage key 和存储元数据；
2. 文件存储由抽象服务管理；
3. 管理员 `can_grant` 可查看、详情和下载全部文档；
4. 普通用户列表仅见符合规则的文档；
5. 普通用户无权详情和下载返回 403；
6. 历史文档无原文件时下载返回 404 File not found；
7. 成功下载写审计日志；
8. `/documents` 可浏览、查看、下载有权限文件；
9. 文档删除能同步删除存储对象；
10. 测试、文档和完整回归通过。
