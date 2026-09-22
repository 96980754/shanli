# 品牌自定义

AI知识库 支持完整的品牌自定义，包括 Logo、组织名称、版权信息、登录协议等，方便企业用户进行品牌定制。

## 品牌信息配置

### 步骤 1：复制模板文件

```bash
cp backend/package/yuxi/config/static/info.template.yaml backend/package/yuxi/config/static/info.local.yaml
```

### 步骤 2：编辑品牌信息

在 `backend/package/yuxi/config/static/info.local.yaml` 中配置你的品牌信息：

- 应用名称
- 组织名称
- Logo
- 版权信息
- 登录页用户协议/隐私协议链接

### 步骤 3：指定配置文件

在 `.env` 中指定配置文件路径：

```env
YUXI_BRAND_FILE_PATH=backend/package/yuxi/config/static/info.local.yaml
```

::: tip 配置优先级
`info.local.yaml` > `info.template.yaml`（默认）
:::

## 英文界面文案

界面切到 English 时，品牌文案取自同名的 `<字段>_en`；该字段缺省或为空则回退中文，因此**只配中文也能正常工作**。

需要双语的字段（定义见 `web/src/stores/info.js` 的 `LOCALIZED_TEXT_KEYS`）：

| 配置项 | 英文键 | 显示位置 |
| :--- | :--- | :--- |
| `organization.name` | `organization.name_en` | 登录页、首页、侧栏 |
| `branding.name` | `branding.name_en` | 登录页、状态栏 |
| `branding.title` | `branding.title_en` | 首页主标题 |
| `branding.subtitle` | `branding.subtitle_en` | 状态栏副标题 |
| `branding.subtitles` | `branding.subtitles_en` | 首页轮播副标题（数组，需整体提供） |
| `footer.copyright` | `footer.copyright_en` | 页脚 |

```yaml
organization:
  name: "AI知识库"
  name_en: "AI Knowledge Base"

branding:
  subtitle: "开源智能体平台套件，融合 RAG 与知识图谱"
  subtitle_en: "An open-source agent platform suite with RAG and knowledge graphs"
```

`footer.copyright` 与 `copyright_en` 都支持 `{{YUXI_VERSION}}` 版本占位符。

## 登录协议配置

登录页支持从品牌配置中读取用户协议与隐私协议链接。

### 配置项

在 `backend/package/yuxi/config/static/info.local.yaml` 的 `footer` 下新增以下字段：

```yaml
footer:
  copyright: "© your org 2026"
  user_agreement_url: "/protocols/user-agreement.template.html"
  privacy_policy_url: "/protocols/privacy-policy.template.html"
```

### 显示规则

- 当 `user_agreement_url` 和 `privacy_policy_url` 都有值时，登录页会显示协议勾选项。
- 任一字段为空时，登录页不显示协议勾选项。
- 未勾选协议时，提交登录/初始化会通过消息提示用户先同意协议。

### 协议模板文件

系统默认提供两个 HTML 模板文件：

- `web/public/protocols/user-agreement.template.html`
- `web/public/protocols/privacy-policy.template.html`

你可以直接编辑这两个文件中的协议内容，并替换占位符（如 `{{ORG_NAME}}`、`{{PRODUCT_NAME}}`、`{{EFFECTIVE_DATE}}`）。

如果你有自己的协议页面，也可以将 `user_agreement_url` 和 `privacy_policy_url` 指向自定义路径或外部链接。

### Icon 定制

系统预设了多种 Icon，如需更多图标，可以从 `lucide-vue-next` 中引入。

## 样式定制

系统支持完整的主题色定制。配置文件位于 `web/src/assets/css/base.css`，以及 `web/src/assets/css/base.dark.css`：

```css
:root {
  --main-color: #1890ff;        /* 主色调 */
  --main-1000: #f0f2f5;          /* 色板 */
  --main-900: #e6f7ff;           /* 色板 */
  /* ... 其他色板 */
}
```

修改配色变量后，界面会实时更新，无需重启服务。

此外，`web/src/stores/theme.js` 中的 `colorPrimary` 也需要同步修改。
