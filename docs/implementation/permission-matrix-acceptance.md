# 甲方分类级权限矩阵验收说明

**日期：** 2026-07-14  
**用途：** 说明甲方提供的角色/资料权限表如何映射到当前系统，并作为 `start_acceptance.sh` 的真实样本验收说明。

---

## 1. 映射原则

本期采用分类级权限，不做文档级权限。

```text
资料分类 = 知识库
访问、下载 = can_view
编辑 = can_upload + can_delete
授权管理 = can_grant
```

因此，甲方表中“文档管理员对某个文档有编辑、下载、访问权限”的描述，在一期验收中收敛为：

```text
资料管理员对某个资料分类知识库有编辑、下载、访问权限。
```

---

## 2. 资料分类

一键验收脚本会创建 6 个知识库：

| 甲方资料 | 当前知识库 |
|---|---|
| POC 产品资料 | `POC产品资料` |
| MCX 产品资料 | `MCX产品资料` |
| 定位产品资料 | `定位产品资料` |
| 产品报价配置表 | `产品报价配置表` |
| 产品规划文档 | `产品规划文档` |
| 客服问答资料库 | `客服问答资料库` |

---

## 3. 角色与账号

统一密码：`Demo12345`。

| 账号 | 角色 | 用途 |
|---|---|---|
| `admin` | 系统管理员 | 管理全部知识库和权限 |
| `kb_poc_admin` | 知识库管理员 | 管理 POC、MCX、定位产品资料 |
| `price_admin` | 文档管理员 | 管理产品报价配置表 |
| `product_manager` | 知识库管理员 | 管理产品规划文档 |
| `support_manager` | 知识库管理员 | 管理客服问答资料库 |
| `sales_cn` | 普通用户 | 国内销售部 |
| `sales_intl` | 普通用户 | 国际销售部 |
| `marketing_support` | 普通用户 | 市场支持部 |
| `product_user` | 普通用户 | 产品管理部 |
| `ops_user` | 普通用户 | 营销运作部 |
| `support_user` | 普通用户 | 客服部 |
| `delivery_user` | 普通用户 | 交付部 |
| `finance_user` | 其它 | 默认不授权，用于反向验证 |

---

## 4. 权限矩阵

| 知识库 | 可访问、下载 | 可编辑、授权 |
|---|---|---|
| `POC产品资料` | 所有普通用户 | `admin`、`kb_poc_admin` |
| `MCX产品资料` | 所有普通用户 | `admin`、`kb_poc_admin` |
| `定位产品资料` | 所有普通用户 | `admin`、`kb_poc_admin` |
| `产品报价配置表` | 国内销售、国际销售、市场支持、产品管理、营销运作 | `admin`、`price_admin` |
| `产品规划文档` | 所有普通用户 | `admin`、`product_manager` |
| `客服问答资料库` | 所有普通用户 | `admin`、`support_manager` |

说明：

- “所有普通用户”包含 `sales_cn`、`sales_intl`、`marketing_support`、`product_user`、`ops_user`、`support_user`、`delivery_user`。
- `finance_user` 不在普通用户资料访问范围内，用于验证无权限场景。
- 当前系统没有组织同步，部门用验收账号模拟。

---

## 5. 真实样本包

脚本读取：

```text
data/examples/AI知识库_数据样本包.zip
```

导入规则：

- `.docx`、`.pdf`：保存原文件，并尽量解析为问答 chunk；
- `.pptx`、`.xlsx`：保存原文件，状态为 `stored_unsupported`，仅支持下载；
- 所有成功导入的文件均应在有权限时 `download_available=true`。

该 ZIP 处理仅用于本地验收数据准备，不是正式产品功能。正式产品流程仍是管理员人工上传筛选后的真实文件。

---

## 6. 验收方式

启动：

```bash
./start_acceptance.sh
```

访问：

```text
http://localhost:8000/login
```

推荐检查：

1. 使用 `admin / Demo12345` 登录，确认能看到 6 个知识库和真实样本文件；
2. 任意点击文档，确认“下载原文件”可用；
3. 使用 `sales_cn / Demo12345` 登录，确认可看普通资料和产品报价配置表；
4. 使用 `delivery_user / Demo12345` 登录，确认看不到产品报价配置表；
5. 使用 `finance_user / Demo12345` 登录，确认无默认资料权限；
6. 检查 PPTX/XLSX 显示“仅可下载（暂不支持内容解析）”。
