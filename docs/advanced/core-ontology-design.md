# Core Ontology Bundle 设计指南

本文面向负责梳理业务知识结构的产品经理、业务专家、知识运营人员和数据治理人员，说明如何设计并上传 Core Ontology Bundle。

Core Ontology 用来回答四个问题：

1. 业务中有哪些类型的对象？
2. 不同对象之间允许建立什么关系？
3. 同一个对象或关系在文档中可能有哪些叫法？
4. 允许抽取哪些结构化属性？

系统会将这些定义作为大模型抽取知识图谱时的白名单。设计过宽会产生噪声，设计过窄会漏掉业务事实，因此应先覆盖稳定、通用、可复用的业务概念，再逐步迭代。

## 1. 推荐方式：通过表单新建

业务人员不需要手工编写 JSON/YAML 或制作 ZIP。superadmin 可以进入“设置 → Core Ontology”，点击“新建 Core Ontology”，依次填写：

- 展示名称、Registry ID 和版本；
- 实体类型、业务说明、示例、标准名称和别名；
- 关系名称、Source、Target 和关系别名；
- 属性分类、属性 key、类型和单位。

点击“创建并启用”后，系统会自动生成并校验四个规范文件，成功后立即加入 Registry。该过程是确定性表单转换，不调用大模型，也不会自动补充或改写业务定义。

admin 可以进入 Core Ontology 设置页查看全部版本的实体、关系、别名、属性和附加规则；只有 superadmin 可以创建、上传或编辑。内置 `V4.1` 和“善理预设”始终只读。

superadmin 可以覆盖自定义 Ontology 的当前 ID/版本，保存后会生成新的 digest。若仍有知识库配置或 Chunk 抽取结果精确引用旧 digest，系统会拒绝覆盖；不会自动清空图谱、删除抽取结果或修改知识库配置。

## 2. Bundle 组成与高级导入

一个 Ontology Bundle 是 ZIP 压缩包，根目录必须且只能包含以下四个 UTF-8 文件：

```text
ontology.zip
├── schema.json
├── entity.yaml
├── relation.yaml
└── property.yaml
```

不要增加说明文档、隐藏文件或外层文件夹。例如，下面的结构不能上传：

```text
ontology.zip
└── ontology/
    ├── schema.json
    ├── entity.yaml
    ├── relation.yaml
    └── property.yaml
```

四个文件的职责分别是：

| 文件 | 职责 | 主要维护者 |
| --- | --- | --- |
| `schema.json` | 定义 Ontology 身份、实体类型、关系类型及关系方向 | 业务架构师、领域专家 |
| `entity.yaml` | 定义不同实体类型下的标准名称和别名 | 知识运营人员 |
| `relation.yaml` | 定义关系类型的自然语言别名 | 业务架构师、知识运营人员 |
| `property.yaml` | 定义允许抽取的属性名称、类型和单位 | 产品专家、数据治理人员 |

## 2. 推荐设计流程

不要先从文件格式出发。建议按以下顺序设计：

1. 收集典型业务问题，例如“某产品支持什么功能”“某方案适用于哪些行业”。
2. 从问题中识别稳定的业务对象，形成实体类型。
3. 定义实体类型之间有明确业务含义的关系及方向。
4. 收集文档中的历史名称、简称、中英文名称，形成别名词典。
5. 识别确实需要结构化查询的参数，形成属性字典。
6. 选择一批真实文档试抽取，检查漏抽、误抽和概念混淆。
7. 修订后提升 `version`，上传新版本。

优先设计能支撑真实查询的问题，不要为了“看起来完整”加入没有使用场景的实体、关系和属性。

## 3. `schema.json`：核心业务结构

### 3.1 最小结构

```json
{
  "registry_id": "shanli-product",
  "version": "1.0.0",
  "name": "山力产品知识本体",
  "status": "active",
  "entities": {
    "Product": {
      "description": "可独立销售的产品或服务",
      "examples": ["F10", "MCSTARS"]
    },
    "Feature": {
      "description": "用户可直接感知的产品能力",
      "examples": ["群组呼叫", "实时定位"]
    }
  },
  "relations": {
    "SUPPORTS": {
      "description": "产品支持某项功能",
      "source": "Product",
      "target": "Feature"
    }
  }
}
```

### 3.2 身份字段

| 字段 | 是否必填 | 说明 |
| --- | --- | --- |
| `registry_id` | 是 | Ontology 的稳定 ID，同一套业务本体的不同版本应保持相同 ID |
| `version` | 是 | 本次 Bundle 的版本号；内容变化时必须提升版本 |
| `name` | 是 | 面向业务人员展示的名称，可以使用中文 |
| `status` | 是 | 完整可用的 Bundle 使用 `active`；仅有空框架时使用 `scaffold` |

`registry_id` 和 `version` 只能包含字母、数字、点、下划线和中划线，长度为 1–64。例如：

```text
shanli-product
product_core
product.v2
1.0.0
2026.07
```

不要使用中文、空格或路径符号。

同一个 `registry_id + version` 只能对应一份内容。若修改任何文件，应提升版本，例如从 `1.0.0` 提升到 `1.0.1`，不能覆盖原版本。

### 3.3 实体类型

`entities` 的 key 是实体类型名称。建议使用稳定、可读的英文 PascalCase 名称：

```json
{
  "entities": {
    "Product": {
      "description": "可独立销售的产品或服务",
      "examples": ["F10", "POCSTARS-MNO"]
    },
    "Scenario": {
      "description": "产品被使用的具体业务场景",
      "examples": ["公安执法", "铁路运输"]
    }
  }
}
```

设计实体类型时遵守以下原则：

- 类型表示一类业务对象，不表示某个具体对象。
- `Product` 是类型，`F10` 是该类型的具体实体。
- 类型之间应尽量互斥，避免同一对象同时难以判断属于多个类型。
- `description` 要写清楚判定边界，而不只是重复类型名称。
- `examples` 应来自真实业务文档，每类提供 2–5 个典型示例即可。
- 不要把颜色、尺寸、版本号等参数设计为实体类型，除非它们需要独立参与关系。

例如，应明确区分：

- `Feature`：用户能够直接感知的能力，如“群组呼叫”“蓝牙连接”。
- `Technology`：实现功能的后台方法，如“WebRTC”“微服务架构”。
- `Scenario`：产品实际被使用的情境，如“公安执法”。
- `Industry`：较稳定的行业分类，如“公共安全”。

### 3.4 关系类型与方向

`relations` 定义允许出现的关系，以及关系两端的实体类型：

```json
{
  "relations": {
    "SUPPORTS": {
      "description": "产品支持某项功能",
      "source": "Product",
      "target": "Feature"
    },
    "USED_IN": {
      "description": "产品用于某个业务场景",
      "source": "Product",
      "target": "Scenario"
    }
  }
}
```

关系名称建议使用大写下划线形式，并用主动、稳定的业务语义命名：

```text
SUPPORTS
USES
USED_IN
BELONGS_TO
COMPLIES_WITH
```

方向必须与业务问题一致。例如：

```text
Product --SUPPORTS--> Feature
```

不要同时定义语义完全相反但可互相推导的两套关系，例如同时定义 `SUPPORTS` 和 `SUPPORTED_BY`，除非业务查询确实需要区分且团队能长期一致维护。

当一个关系允许多个起点或终点类型时，可以使用数组：

```json
{
  "RELATED_TO_DOCUMENT": {
    "source": ["Product", "Solution"],
    "target": "Document",
    "description": "产品或方案关联到来源文档"
  }
}
```

也可以使用 `Any` 表示任意已声明实体类型，但应谨慎使用：

```json
{
  "SUPPORTED_BY": {
    "source": "Any",
    "target": "Evidence",
    "description": "业务事实由证据支撑"
  }
}
```

`Any` 会显著放宽约束，只适合证据、文档等真正跨类型的通用关系。

## 4. `entity.yaml`：实体标准名和别名

文件必须有顶层 `entities`：

```yaml
entities:
  Product:
    MCSTARS:
      - MCSTARS
      - MCX系统
      - MCSTARS平台
    POCSTARS-MNO:
      - POCSTARS-MNO
      - POCSTARS MNO
      - MNO公网对讲平台

  Feature:
    组呼:
      - 组呼
      - 群组呼叫
      - 群组对讲
      - Group Call
```

层级含义为：

```text
实体类型 → 标准名称 → 别名列表
```

抽取结果命中别名后，会归一化为标准名称。例如，文档中的“MCX系统”和“MCSTARS平台”最终都归一化为 `MCSTARS`。

维护词典时：

- 第一层实体类型必须已在 `schema.json.entities` 中声明。
- 标准名称应是业务团队统一采用的展示名称。
- 别名应包含真实出现的简称、旧称、中英文名称和常见书写差异。
- 可以把标准名称本身放入别名列表，便于阅读，但不是强制要求。
- 同一实体类型中，一个别名不能同时映射到两个标准名称。
- 不要加入过于宽泛的词，如“系统”“平台”“产品”，它们容易造成错误归一化。
- 不要把类型名称当成具体实体，例如不要把所有“产品”归一化为某一个产品名称。

若某种实体暂时没有标准名称词典，也必须保留空对象：

```yaml
entities:
  Product: {}
  Feature: {}
```

## 5. `relation.yaml`：关系自然语言别名

文件必须有顶层 `relations`：

```yaml
relations:
  SUPPORTS:
    aliases:
      - 支持
      - 具备
      - 提供
      - 集成

  USES:
    aliases:
      - 使用
      - 采用
      - 基于
      - 依赖
```

层级含义为：

```text
标准关系类型 → aliases → 自然语言表达列表
```

关系类型必须已在 `schema.json.relations` 中声明。系统以 `schema.json` 中的 `source`、`target` 和 `description` 为结构依据；`relation.yaml` 只维护语言映射，因此不要在这里重复维护方向。

维护原则：

- 别名应是文档中真实表达该关系的动词或短语。
- 同一个别名不能映射到两个不同关系。
- 谨慎加入“有”“是”“相关”等泛化词，它们通常无法稳定表达具体关系。
- “支持”可能表示支持功能、支持部署方式或提供技术支持，应根据上下文拆分，避免同时加入多个关系。
- 关系方向仍由 `schema.json` 决定，别名不会改变方向。

没有别名时使用：

```yaml
relations: {}
```

## 6. `property.yaml`：结构化属性字典

文件必须有顶层 `properties`：

```yaml
properties:
  Hardware:
    screen_size:
      type: float
      unit: inch
    battery_capacity:
      type: int
      unit: mAh

  Network:
    bluetooth:
      type: string
    frequency:
      type: string
      unit: MHz/GHz
```

层级含义为：

```text
属性分类 → 属性 key → 类型与单位
```

属性分类主要用于组织和阅读；抽取结果实际使用的是属性 key，因此所有分类下的属性 key 必须全局唯一。

当前支持的 `type`：

| 类型 | 适用值 | 示例 |
| --- | --- | --- |
| `string` | 文本或复合格式 | `IP68`、`1920x1080` |
| `int` / `integer` | 整数 | `5000` |
| `float` / `number` | 小数或整数 | `2.4` |
| `bool` / `boolean` | 布尔值 | `true`、`false`、`是`、`否` |

`unit` 可选。填写单位后，系统校验数值时允许值中带该单位，例如：

```yaml
screen_size:
  type: float
  unit: inch
```

以下值均可通过数值校验：

```text
2.4
2.4inch
```

设计属性时：

- 只定义需要筛选、比较或结构化展示的参数。
- key 使用稳定的英文 snake_case，例如 `battery_capacity`。
- 同一个业务含义只保留一个 key，避免同时存在 `battery`、`battery_size`、`battery_capacity`。
- 数值应尽量拆分数值和单位；格式复杂或经常包含范围时可使用 `string`。
- 当前不要使用 `enum`、日期、数组或嵌套对象等未支持类型。
- `description`、`required` 等额外字段当前不会参与抽取校验；关键语义应体现在清晰的 key、类型和单位中。

没有属性时使用：

```yaml
properties: {}
```

## 7. 一个完整的最小示例

### `schema.json`

```json
{
  "registry_id": "product-core",
  "version": "1.0.0",
  "name": "产品知识本体",
  "status": "active",
  "entities": {
    "Product": {
      "description": "可独立销售的产品",
      "examples": ["F10"]
    },
    "Feature": {
      "description": "用户可直接感知的产品能力",
      "examples": ["蓝牙", "组呼"]
    }
  },
  "relations": {
    "SUPPORTS": {
      "description": "产品支持某项功能",
      "source": "Product",
      "target": "Feature"
    }
  }
}
```

### `entity.yaml`

```yaml
entities:
  Product:
    F10:
      - F10
      - F10终端
  Feature:
    组呼:
      - 组呼
      - 群组呼叫
      - 群组对讲
```

### `relation.yaml`

```yaml
relations:
  SUPPORTS:
    aliases:
      - 支持
      - 具备
      - 提供
```

### `property.yaml`

```yaml
properties:
  Hardware:
    screen_size:
      type: float
      unit: inch
```

对于文本“F10 终端具备 2.4 英寸屏幕并支持群组呼叫”，预期抽取结果应表达：

```text
实体：F10（Product）
实体：组呼（Feature）
关系：F10 --SUPPORTS--> 组呼
属性：screen_size = 2.4inch
```

## 8. Core Ontology 与领域扩展的边界

Core Ontology 应保存跨知识库、跨文档长期稳定的结构，例如统一的产品、功能、技术、行业和场景定义。

知识库图谱配置中的“领域 Ontology 扩展”适合保存只对当前知识库有效的类型或关系。领域扩展只能新增内容，不能覆盖 Core 中已有的实体类型、关系、词典或属性分类。

建议放入 Core 的内容：

- 企业统一产品分类。
- 跨部门共用的行业、场景和能力定义。
- 已形成治理标准的关系和属性。
- 需要在多个知识库间保持一致的标准名称。

建议放入领域扩展的内容：

- 单个项目特有的对象。
- 单个客户或区域特有的分类。
- 尚在试验、未形成企业统一标准的关系。

## 9. 版本管理与变更规则

上传成功的 Bundle 会立即可用，但系统不会覆盖同 ID、同版本的已有内容。因此每次内容发生变化都必须提升 `version`。

建议采用语义化版本：

- `1.0.0 → 1.0.1`：补充别名、修正文案等兼容性调整。
- `1.0.0 → 1.1.0`：增加实体类型、关系或属性。
- `1.0.0 → 2.0.0`：删除、重命名类型，或改变关系方向等不兼容调整。

以下变化可能影响已有图谱的一致性：

- 实体类型重命名或删除。
- 关系类型重命名、删除或改变方向。
- 标准实体名称改变。
- 属性 key 或类型改变。

已有索引、抽取缓存、实体或关系的知识库不能直接切换 Core Ontology。必须先在图谱管理中执行显式重置，清空图谱和抽取结果，再选择新版本重新构建。

上传前建议保留每个版本的源文件和变更记录，不要只保存 ZIP。

## 10. 上传限制

上传时系统会校验：

- ZIP 压缩包不超过 5 MiB。
- 只能包含四个根目录普通文件。
- 单个文件不超过 4 MiB。
- 解压后总大小不超过 8 MiB。
- 文件必须为 UTF-8。
- 不允许目录、外层文件夹、符号链接、加密文件或危险路径。
- JSON/YAML 必须是对象结构。
- 实体、关系、别名、属性和引用必须相互一致。
- 同一 ID 和版本不能上传不同内容。

上传入口位于“设置 → Core Ontology”，仅 superadmin 可以上传；admin 可以在知识库图谱配置中读取并选择已经上传的 Bundle。

## 11. 常见错误

### `Ontology.registry_id 必须是非空字符串`

`schema.json` 缺少顶层 `registry_id`，或者其值为空、不是字符串。

正确写法：

```json
{
  "registry_id": "shanli-product"
}
```

### `Ontology.status 必须是非空字符串`

缺少 `status`。完整可用的 Bundle 应填写：

```json
{
  "status": "active"
}
```

### `entity.yaml 的 entities 必须是对象`

文件缺少 `entities:` 顶层节点。错误写法：

```yaml
Product:
  F10:
    - F10终端
```

正确写法：

```yaml
entities:
  Product:
    F10:
      - F10终端
```

### `relation.yaml 的 relations 必须是对象`

文件缺少 `relations:` 顶层节点，或者关系名称没有在 `schema.json` 中声明。

### `property.yaml 的 properties 必须是对象`

文件缺少 `properties:` 顶层节点，或使用了不支持的属性类型。

### `引用未声明实体`

关系的 `source` 或 `target` 使用了未在 `entities` 中定义的类型。先补实体类型，或修正拼写和大小写。

### `同一版本已存在`

已经上传过相同 `registry_id + version`，但新 ZIP 内容不同。请提升 `version`，不要尝试覆盖已有版本。

### `ZIP 必须且只能包含四个根目录文件`

常见原因包括：

- 压缩时多套了一层目录。
- ZIP 中带有 `.DS_Store`、`Thumbs.db` 或说明文件。
- 文件名大小写或扩展名不正确。

## 12. 发布前检查清单

### 业务结构

- [ ] 每个实体类型都能用一句话说明“什么属于它、什么不属于它”。
- [ ] 实体类型之间没有明显重叠或同义重复。
- [ ] 每个关系都有真实业务查询场景。
- [ ] 每个关系的 source、target 和方向已经由领域专家确认。
- [ ] 没有滥用 `Any` 或过于宽泛的关系。

### 词典

- [ ] 标准名称是企业希望统一展示的名称。
- [ ] 别名来自真实文档，而不是凭空枚举。
- [ ] 同一类型内没有一个别名指向多个标准名称。
- [ ] 没有“系统”“平台”“产品”等过于宽泛的别名。

### 属性

- [ ] 每个属性都确实需要结构化查询或展示。
- [ ] 属性 key 全局唯一且使用 snake_case。
- [ ] 类型只使用当前支持的类型。
- [ ] 数值属性的单位统一。

### 文件与版本

- [ ] `schema.json` 包含 `registry_id`、`version`、`name`、`status`、`entities`、`relations`。
- [ ] 三个 YAML 文件分别包含 `entities`、`relations`、`properties` 顶层节点。
- [ ] 四个文件均为 UTF-8。
- [ ] 修改已有内容后已经提升 `version`。
- [ ] ZIP 根目录只有四个指定文件，没有外层目录或隐藏文件。
- [ ] 已使用真实文档做过试抽取并人工检查结果。
