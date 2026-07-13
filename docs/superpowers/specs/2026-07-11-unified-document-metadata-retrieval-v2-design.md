# 知识库统一元数据与检索策略 V2 设计

**日期：** 2026-07-11
**状态：** 已确认，进入实施计划
**定位：** 元数据事实层与检索策略层分离的企业级 RAG 设计；适用于一期约 283 份文档，并兼容向量库、知识图谱、后台与后续权限扩展。

---

## 1. 目标与核心原则

本设计建立一套统一的 `Document Metadata` 模型，使文档分类、权限过滤、检索重排、图谱可见性和运营统计共享同一事实来源。

1. **Metadata 描述事实，不描述检索策略。** 文档只记录其可见范围、类型、产品归属、运营优先级及 ACL；不保存“排第几”或某个检索算法的分数。
2. **检索策略配置化。** 权重和 Top-K 在策略配置中维护；调优不改历史文档、不重入库。
3. **权限前置过滤，排序后置优化。** 所有权限约束必须在召回候选集形成前执行；`type`、查询产品匹配和 `priority` 只用于候选集内重排。
4. **图谱实体保持业务纯净。** Entity/Relation 不复制权限或分类字段；通过来源 Chunk 与 Document 的元数据判断是否可见。
5. **Document Metadata 是系统唯一权限来源（SSOT）。** 向量库、知识图谱、后台管理和统计分析均读取同一套文档元数据，不维护互相独立、可能漂移的 ACL 副本。
6. **产品分类与产品权限分离。** `product` 默认是文档事实分类和检索排序依据；只有有效知识视图明确给出 `allowed_products` 时，才将它加入权限 Filter。

---

## 2. 规范化文档元数据

### 2.1 逻辑模型

```json
{
  "scope": "I",
  "type": "WP",
  "product": "MC",
  "priority": "P0",
  "security_level": 1,
  "acl": {
    "roles": []
  }
}
```

| 字段 | 语义 | 权限/检索用途 | 稳定性 |
|---|---|---|---|
| `scope` | 客户、内部或受限可见范围 | 硬 Filter | 稳定 |
| `type` | 文档业务类型 | ReRank | 非常稳定 |
| `product` | 文档所属产品线 | 默认 ReRank；存在产品授权范围时为硬 Filter | 稳定 |
| `priority` | 核心、重要、参考的运营属性 | ReRank | 可调整 |
| `security_level` | 文档密级，数值越高越敏感 | 硬 Filter | 稳定 |
| `acl.roles` | 角色级 ACL 扩展点 | 后续硬 Filter | 一期预留 |

### 2.2 Scope 编码

| 编码 | 含义 |
|---|---|
| `C` | Customer，客户可见 |
| `I` | Internal，内部可见 |
| `R` | Restricted，受限可见 |

### 2.3 Type 编码

| 编码 | 显示名 | 说明 |
|---|---|---|
| `WP` | 白皮书 | 产品或技术白皮书 |
| `PI` | 产品介绍 | PPT 产品介绍 |
| `UM` | 用户手册 | Client、调度台等用户手册 |
| `DG` | 部署指南 | 服务部署指南 |
| `SOL` | 解决方案 | 方案 PPT |
| `SM` | 销售话术 | 一纸禅、Quick Guide |
| `SLA` | SLA | 服务等级协议 |
| `IG` | 安装手册 | 安装指南 |
| `OM` | 运维管理 | 日常运维规范 |
| `FT` | 故障处理 | 故障处理规范 |
| `SPEC` | 产品规格/彩页 | 规格书、彩页 |
| `QG` | 快速指南 | 快速适配工具手册 |
| `FL` | 功能清单 | Excel 功能清单 |
| `CP` | 竞品分析 | 竞品差异化分析 |
| `OTH` | 其他 | 零散文件 |
| `CERT` | 认证/合规 | Plugtests、证书等 |
| `IMG` | 图片/Logo | Logo、证书图片等 |

### 2.4 Product 编码

| 编码 | 含义 |
|---|---|
| `MC` | MCSTARS |
| `MS` | MiniServer |
| `MD` | MDM |
| `MNO` | POCSTARS-MNO |
| `PRO` | POCSTARS-PRO |
| `UC` | POCSTARS-UC |
| `LOC` | 定位产品 |
| `GEN` | 通用 |

### 2.5 Priority 编码

| 编码 | 含义 |
|---|---|
| `P0` | 核心知识 |
| `P1` | 重要知识 |
| `P2` | 参考知识 |

---

## 3. 当前实现的兼容映射

当前 `Document` 已有元数据与 V2 字段的映射如下：

| 当前字段 | V2 字段 | 演进策略 |
|---|---|---|
| `visibility`（`public/internal/restricted`） | `scope`（`C/I/R`） | 新增规范字段 `scope`，迁移时按 `public → C`、`internal → I`、`restricted → R` 映射；旧字段暂保留兼容 API。 |
| `product_line` | `product` | 新增规范产品编码字段；旧自由文本保留展示与导入兼容。 |
| `department` | 不属于 V2 基础分类 | 保留为组织维度；有效视图需要时作为硬 Filter。 |
| `security_level` | `security_level` | 原样保留，始终是硬 Filter。 |
| `tags` | 辅助检索/展示标签 | 保留，不作为一期开关权限的唯一依据。 |
| 无 | `type` | 新增规范类型编码。 |
| 无 | `priority` | 新增运营优先级。 |
| `KnowledgeViewRule` | effective view / ACL 输入 | 后续扩展 `allowed_scopes`、`allowed_products`、`allowed_roles`；文档访问与 RAG 仅依赖最终有效规则。 |

现有知识库 `can_view` 仍是第一道门槛：无此权限直接拒绝。`can_grant` 用户仍可绕过用户级知识视图规则；该管理绕过不等于绕过未来系统级强制密级或合规策略。

---

## 4. 有效知识视图与权限过滤

### 4.1 授权判断顺序

```text
用户是否拥有知识库 can_view？
├─ 否 → 拒绝访问/不进入召回
└─ 是
   ├─ 是否拥有 can_grant？
   │  └─ 是 → 允许用户级知识视图规则范围内的全部文档
   └─ 否
      ├─ 计算 effective_view_rule
      │  ├─ 用户知识库规则
      │  ├─ 项目/部门规则（后续）
      │  ├─ 角色/等级规则（后续）
      │  └─ 系统默认规则（后续）
      └─ 使用以下硬 Filter：
         scope ∈ allowed_scopes
         AND security_level ≤ max_security_level
         AND department ∈ allowed_departments（配置时）
         AND product ∈ allowed_products（配置时）
         AND acl.roles 与用户角色相交（启用后）
```

无用户级规则时，当前阶段继承现有“知识库内全量可见”语义，但仍要满足系统强制规则（例如未来的密级上限）。空数组表示该维度不限制，不表示拒绝全部。

### 4.2 Product 权限边界

- 用户问题识别出 `MC`，只意味着相关 `MC` 文档在 ReRank 中获得匹配加分。
- 用户被授权 `allowed_products=["MC"]`，才意味着 `product="MC"` 是召回前硬 Filter。
- `GEN` 通用文档的可见性由显式策略控制；一期默认不因为产品 Filter 自动放行，以避免意外跨产品资料泄漏。后续可引入 `include_general_product_documents` 有效视图开关。

### 4.3 Filter 输出契约

无论当前数据库临时检索、后续 Milvus 或图谱查询，必须以同一个结构消费过滤结果：

```python
@dataclass(frozen=True)
class EffectiveDocumentFilter:
    allowed_scopes: set[str] | None
    allowed_departments: set[str] | None
    allowed_products: set[str] | None
    allowed_roles: set[str] | None
    max_security_level: int | None
```

`None` 表示该维度不限制，空集合表示显式拒绝该维度的全部值。此约定与现有“空数组不限制”规则不同：外部配置层仍可使用空数组表示未设置，规则归并层必须显式转换为 `None` 或空集合，避免歧义。

---

## 5. 检索策略层

### 5.1 策略配置

新增逻辑配置文件：

```text
backend/config/retrieval_policy.yaml
```

```yaml
type_weight:
  WP: 1.0
  PI: 1.0
  UM: 0.9
  DG: 0.8
  SOL: 0.8
  SM: 0.8
  SLA: 0.7
  IG: 0.7
  OM: 0.6
  FT: 0.6
  SPEC: 0.6
  QG: 0.5
  FL: 0.5
  CP: 0.5
  OTH: 0.3
  CERT: 0.2
  IMG: 0.0

product_weight:
  MC: 1.0
  MS: 0.9
  MD: 0.9
  MNO: 0.9
  PRO: 0.9
  UC: 0.9
  LOC: 0.9
  GEN: 0.8

priority_boost:
  P0: 1.2
  P1: 1.0
  P2: 0.8

formula:
  similarity_ratio: 0.75
  type_ratio: 0.10
  product_ratio: 0.10
  priority_ratio: 0.05

top_k:
  initial: 100
  after_rerank: 20
  final: 10
```

策略文件只定义检索偏好，不保存权限策略。运行时应在每次查询或配置变更监测后重新读取；一期可采用每次查询读取，以优先保证“修改立即生效”。

### 5.2 分数与产品识别

```text
FinalScore = similarity_ratio × SimilarityScore
           + type_ratio       × TypeWeight
           + product_ratio    × ProductWeight
           + priority_ratio   × PriorityBoost
```

- `SimilarityScore` 由向量检索/当前临时检索产生，必须归一化到 `[0, 1]` 后参与公式。
- 未识别出产品时，产品项为 `0`，而非给所有产品相同加分，避免无意义地重排。
- 识别出产品时，仅文档 `product` 与查询产品相等的候选使用其 `product_weight`；不匹配候选的产品项为 `0`。
- 缺失或未知 `type`、`product`、`priority` 的旧文档使用 `OTH`、`GEN`、`P2` 的默认值，确保历史数据仍可检索。
- 策略加载时必须验证四个公式系数均非负且总和为 `1.0`；未知编码必须有确定的回退行为。

### 5.3 一期产品识别

一期不引入独立 NER 服务。实现一个可配置的轻量别名识别器：

```python
PRODUCT_ALIASES = {
    "MC": ["MCSTARS", "MC STARS"],
    "MS": ["MiniServer", "Mini Server"],
    "MD": ["MDM"],
    "MNO": ["POCSTARS-MNO", "MNO"],
    "PRO": ["POCSTARS-PRO", "PRO"],
    "UC": ["POCSTARS-UC", "UC"],
    "LOC": ["定位"],
}
```

同一问题匹配多个产品时，不在一期隐式猜测唯一产品：向 ReRank 传入全部匹配产品集合，任一匹配均获得加分。后续需要实体消歧时，再替换为 NER/词典服务，不改变检索服务接口。

---

## 6. 检索链路

```text
用户问题 + 已认证用户
    ↓
Step 1：解析用户身份、角色与 effective_view_rule
    ↓
Step 2：构建 EffectiveDocumentFilter（硬隔离）
    ↓
Step 3：产品别名识别，得到 matched_products
    ↓
Step 4：按 Filter 获取可访问 Document / Chunk 候选
  当前临时数据库链路：DocumentChunk JOIN Document 后过滤
  后续 Milvus：expr/metadata filter 后向量 Top-K
    ↓
Step 5：初始召回（initial=100）
    ↓
Step 6：加载 retrieval_policy.yaml，元数据重排
    ↓
Step 7：保留 after_rerank=20，向生成器提供 final=10
    ↓
LLM 生成回答与可访问来源
```

权限过滤不能由 LLM、提示词或后置截断承担；无论是 BM25、向量检索还是图谱检索，所有候选来源必须先通过同一有效文档 Filter。

---

## 7. 向量库与图谱一致性

### 7.1 一期 Chunk 冗余元数据

一期约数千 Chunk，可在向量库每个 Chunk 复制必要元数据：

```json
{
  "doc_id": "MCSTARS-001",
  "chunk_index": 3,
  "content": "MCSTARS 是善理通益推出的关键任务通信系统……",
  "scope": "I",
  "type": "WP",
  "product": "MC",
  "priority": "P0",
  "security_level": 1,
  "acl": { "roles": [] }
}
```

写入时只能从 `Document Metadata` 派生。更新 Document 的权限相关字段后，必须发布“同步 Chunk 元数据”的任务；在同步完成前，在线权限判断仍以关系型 `Document` 元数据为准，禁止仅信任可能滞后的向量副本。

### 7.2 后续集中元数据演进

文档数万、Chunk 百万级时，允许将 Chunk 缩减为 `doc_id` 与检索字段，将元数据集中在 Document 表/缓存。检索服务仍接收 `EffectiveDocumentFilter` 和统一的候选元数据视图，因此不改变调用方接口。

### 7.3 图谱可见性

```text
Entity / Relation
    ← source Chunk
    ← source Document Metadata
    ← EffectiveDocumentFilter
```

- Entity 与 Relation 不新增 `scope/type/product/priority/acl` 字段。
- 一条实体或关系若有多个来源文档，只要至少存在一个当前用户可见来源即可返回；响应需携带可见 `source_document_ids`。
- 只存在不可见来源的实体/关系不返回，也不得在聚合、计数、自动补全或错误提示中泄露名称。

---

## 8. 后台与迁移策略

### 8.1 后台能力

管理台新增或调整：

- 文档上传与详情：`scope`、`type`、`product`、`priority`、`security_level`；保留部门和辅助标签；
- 文档列表：显示规范编码与显示名；
- 批量编辑：一期只设计接口，不在本轮实现 UI；
- 检索策略：只读展示当前生效的 `retrieval_policy.yaml` 和公式；策略编辑权限与文档元数据权限分离，后续单独定义。

### 8.2 历史文档迁移默认值

| V2 字段 | 当前历史值/默认值 |
|---|---|
| `scope` | `visibility` 映射，空值为 `I` |
| `type` | `OTH` |
| `product` | `product_line` 映射，未识别为 `GEN` |
| `priority` | `P2` |
| `security_level` | 已有值，缺失为 `1` |
| `acl.roles` | `[]` |

一期新增字段使用带默认值的列，避免因已有 SQLite/测试数据导致迁移中断。当前 `ensure_runtime_schema()` 在 SQLite/PostgreSQL 的数据库模式启动时对已有 `documents` 表执行幂等补列；正式生产部署后仍应迁移到 Alembic 版本化数据迁移，并按上述默认值回填。

---

## 9. 范围边界

本阶段包含：

1. V2 文档元数据字段和当前字段兼容映射；
2. 有效文档硬 Filter 模型；
3. 数据库临时 RAG 链路按文档元数据过滤；
4. 可配置策略加载、轻量产品别名识别与元数据重排；
5. 文档与检索服务单元/集成测试；
6. 管理台最小元数据编辑与策略只读展示；
7. API 和技术映射文档更新。

本阶段不包含：

- Milvus 实际部署或 Milvus 查询实现；
- Neo4j/LightRAG 实际图谱写入与图查询；
- 独立 NER 模型、实体消歧服务；
- 多租户隔离；
- 完整 ABAC；
- 自动分类、Taxonomy Service 或标签词典；
- 文档版本治理；
- 批量编辑 UI；
- 策略文件的在线写入/热更新管理台；
- MinIO、文件下载或 `/documents` 页面功能实现（已有权限文档库计划保持暂停，待本阶段完成后重新衔接）。

---

## 10. 验收标准

1. `Document` 有 `scope/type/product/priority` 规范字段，旧字段映射和历史默认值明确；
2. 未通过知识库 `can_view`、scope、部门、产品、角色 ACL 或密级规则的文档/Chunk 不进入 RAG 候选集；
3. `can_grant` 用户绕过用户级规则，但不能绕过未来系统强制安全规则；
4. 同一可见候选集内，策略文件修改可改变排序且无需重入库；
5. 未识别产品不产生产品加分；识别一个或多个产品时，仅匹配文档加分；
6. 所有公式配置与未知编码均有可测试的验证/回退行为；
7. 现有问答、知识视图规则、文档上传/详情回归不受破坏；
8. 文档、检索、向量和图谱的权限来源均指向 Document Metadata SSOT；
9. API 文档、技术映射与测试记录同步更新。
