# 真实样本包与分类级权限验收实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将一键验收脚本改为使用 `data/examples/AI知识库_数据样本包.zip` 中的真实资料，并按甲方“分类级资料权限”初始化知识库、用户和权限。

**架构：** 不新增产品级 ZIP 导入 API，不改变正式“管理员人工上传”流程；仅增强本地验收脚本 `start_acceptance.sh`。脚本启动前重建验收数据库，从 ZIP 读取真实文件，按文件名映射到 6 个资料知识库，写入文件存储和数据库元数据；DOCX/PDF 尽量解析，PPTX/XLSX 仅下载。

**技术栈：** Bash、Python 标准库 `zipfile`、SQLAlchemy、FastAPI 启动环境变量、现有 `IngestionService` / `LocalFileStorageService` / 权限模型。

---

## 文件结构与职责

| 文件 | 变更 | 职责 |
|---|---|---|
| `start_acceptance.sh` | 修改 | 一键准备真实样本数据、甲方角色用户、分类级知识库权限并启动服务。 |
| `docs/implementation/permission-matrix-acceptance.md` | 创建 | 记录甲方权限表到当前系统分类级权限模型的映射、验收账号和限制。 |

---

### 任务 1：将一键启动脚本切换为真实样本包

**文件：**
- 修改：`start_acceptance.sh`

- [ ] **步骤 1：确认当前脚本使用假样本**

运行：

```bash
grep -n "manual.txt\|presentation sample\|spreadsheet sample" start_acceptance.sh
```

预期：能看到 3 个硬编码样本文档。

- [ ] **步骤 2：替换为 ZIP 读取与分类映射**

在脚本内 Python 初始化段中：

```python
sample_zip = root_dir / "data" / "examples" / "AI知识库_数据样本包.zip"
CATEGORY_RULES = [
    ("定位产品资料", ["定位"]),
    ("客服问答资料库", ["故障", "运维", "SLA", "服务等级"]),
    ("产品报价配置表", ["销售", "话术"]),
    ("产品规划文档", ["白皮书", "产品介绍", "解决方案", "功能清单"]),
    ("MCX产品资料", ["MCX"]),
    ("POC产品资料", ["MCSTARS", "MiniServer"]),
]
```

每个 ZIP 成员写入 `LocalFileStorageService` 根目录对应 `storage_key`，并写 `Document` 元数据。

- [ ] **步骤 3：按格式设置解析状态**

```python
if suffix in {".pptx", ".xlsx"}:
    document.status = "stored_unsupported"
else:
    document.status = "parsed"
    IngestionService(session, storage).ingest_uploaded_document(document)
```

如果真实 DOCX/PDF 解析失败，不中断启动：保留原件，状态降级为 `stored_unsupported`，确保可下载。

- [ ] **步骤 4：创建分类级用户和权限**

账号统一密码 `Demo12345`。创建：

```text
admin
kb_poc_admin
price_admin
product_manager
support_manager
sales_cn
sales_intl
marketing_support
product_user
ops_user
support_user
delivery_user
finance_user
```

按甲方分类表写 `KnowledgeBasePermission`：

- 所有普通用户可访问：POC、MCX、定位、产品规划、客服问答；
- 报价配置表仅国内销售、国际销售、市场支持、产品管理、营销运作可访问；
- 分类管理员对负责知识库拥有 `can_view/can_upload/can_delete/can_grant=true`；
- `finance_user` 默认不授权，用于反向验证。

- [ ] **步骤 5：手动启动验证**

运行：

```bash
./start_acceptance.sh
```

预期输出列出：访问地址、账号密码、6 个知识库、导入文件数量。

---

### 任务 2：验证真实样本下载与权限

**文件：**
- 修改：无

- [ ] **步骤 1：管理员 API 验证**

运行：

```bash
python3 - <<'PY'
import httpx
base = 'http://localhost:8000'
token = httpx.post(base + '/api/auth/login', json={'username': 'admin', 'password': 'Demo12345'}).json()['token']
headers = {'Authorization': f'Bearer {token}'}
kbs = httpx.get(base + '/api/kb', headers=headers).json()['items']
assert len(kbs) == 6, kbs
for kb in kbs:
    docs = httpx.get(f"{base}/api/kb/{kb['id']}/documents", headers=headers).json()['items']
    assert docs, kb
    for doc in docs:
        assert doc['download_available'] is True, doc
print('ok')
PY
```

预期：输出 `ok`。

- [ ] **步骤 2：下载接口验证**

对每个知识库第一篇文档请求下载，预期返回 `200` 和附件头。

- [ ] **步骤 3：普通用户权限验证**

使用 `finance_user / Demo12345` 登录，预期 `/api/kb` 返回空或不包含 6 个受控知识库。

---

### 任务 3：记录甲方权限映射说明

**文件：**
- 创建：`docs/implementation/permission-matrix-acceptance.md`

- [ ] **步骤 1：编写文档**

文档包含：

- 分类级原则：资料 = 知识库；
- `can_view` 表示访问和下载；
- `can_upload/can_delete` 表示编辑；
- `can_grant` 表示授权管理；
- 6 个资料分类；
- 验收账号；
- 不做文档级权限；
- 不做组织同步，先用账号模拟部门。

- [ ] **步骤 2：检查文档没有 ZIP 产品功能承诺**

运行：

```bash
grep -n "产品功能\|正式导入" docs/implementation/permission-matrix-acceptance.md || true
```

预期：没有把 ZIP 验收脚本描述为正式产品功能。

---

## 自检

- 规格覆盖：真实样本包、6 个资料分类、分类级权限、下载、PPTX/XLSX 仅下载均有任务覆盖。
- 范围控制：不新增文档级权限，不新增组织同步，不新增正式 ZIP 导入 API。
- 验证：脚本启动 + API 下载 + 普通用户权限反向验证。
