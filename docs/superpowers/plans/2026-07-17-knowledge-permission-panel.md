# 知识库权限编辑前端实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 完善 AI知识库 知识库详情页的权限设置面板，让管理员可以按用户、部门、角色选择授权对象，并用常用预设快速配置 8 个知识库操作权限。

**架构：** 保留现有 `KnowledgePermissionPanel.vue` 和 `knowledge_api.js` 的后端接口调用，不新增后端接口。权限面板在组件内加载用户与部门选项，按 `subject_type` 切换选择器，保存时仍向现有 `PUT /api/knowledge/databases/{kb_id}/permissions` 提交 `subject_type`、`subject_id` 和 8 个布尔权限位。

**技术栈：** Vue 3 `<script setup>`、Ant Design Vue、现有 `databaseApi`、`authApi.getUserAccessOptions`、`departmentApi.getDepartments`、Node `assert` 前端纯函数测试、Vite 构建验证。

---

## 文件结构

- 创建：`web/src/components/knowledgePermissionPanelHelpers.js`
  - 职责：抽出权限位、权限预设、对象 label 格式化、payload 构建等纯函数，降低 `.vue` 文件复杂度并支持 TDD。
- 创建：`web/src/components/__tests__/knowledgePermissionPanelHelpers.test.js`
  - 职责：用 Node `assert` 测试权限面板纯函数行为。
- 修改：`web/package.json`
  - 职责：增加 `test:permissions` 脚本，执行本轮新增前端测试。
- 修改：`web/src/components/KnowledgePermissionPanel.vue`
  - 职责：知识库权限矩阵展示与授权编辑；新增授权对象选择器、权限预设、对象名称展示、用户/部门数据加载。
- 不修改：`web/src/apis/knowledge_api.js`
  - 原因：已有 `getPermissions`、`upsertPermission`、`deletePermission` 已满足本轮需求。
- 不修改：`web/src/apis/auth_api.js`
  - 原因：已有 `authApi.getUserAccessOptions()` 可加载可授权用户。
- 不修改：`web/src/apis/department_api.js`
  - 原因：已有 `departmentApi.getDepartments()` 可加载部门列表。

---

### 任务 1：抽出权限面板纯函数并补测试

**文件：**
- 创建：`web/src/components/knowledgePermissionPanelHelpers.js`
- 创建：`web/src/components/__tests__/knowledgePermissionPanelHelpers.test.js`
- 修改：`web/package.json`

- [ ] **步骤 1：编写失败的测试**

创建 `web/src/components/__tests__/knowledgePermissionPanelHelpers.test.js`：

```js
import assert from 'node:assert/strict'

import {
  buildPermissionPayload,
  formatSubjectLabel,
  permissionKeys,
  permissionPresets,
  resetPermissionFlags
} from '../knowledgePermissionPanelHelpers.js'

const run = () => {
  assert.deepEqual(permissionKeys, [
    'can_view',
    'can_search',
    'can_upload',
    'can_download',
    'can_delete',
    'can_manage',
    'can_grant',
    'can_export'
  ])

  assert.deepEqual(permissionPresets.readonly.flags, {
    can_view: true,
    can_search: true,
    can_upload: false,
    can_download: false,
    can_delete: false,
    can_manage: false,
    can_grant: false,
    can_export: false
  })

  assert.deepEqual(permissionPresets.editor.flags, {
    can_view: true,
    can_search: true,
    can_upload: true,
    can_download: true,
    can_delete: false,
    can_manage: false,
    can_grant: false,
    can_export: false
  })

  assert.deepEqual(permissionPresets.manager.flags, {
    can_view: true,
    can_search: true,
    can_upload: true,
    can_download: true,
    can_delete: true,
    can_manage: true,
    can_grant: true,
    can_export: true
  })

  assert.deepEqual(resetPermissionFlags({ can_view: true, can_grant: true }), {
    can_view: true,
    can_search: false,
    can_upload: false,
    can_download: false,
    can_delete: false,
    can_manage: false,
    can_grant: true,
    can_export: false
  })

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'user', subject_id: 'zhangsan' },
      {
        users: [{ uid: 'zhangsan', username: '张三' }],
        departments: []
      }
    ),
    '张三（zhangsan）'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'department', subject_id: '10' },
      {
        users: [],
        departments: [{ id: 10, name: '研发部' }]
      }
    ),
    '研发部（10）'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'role', subject_id: 'admin' },
      {
        users: [],
        departments: []
      }
    ),
    '管理员（admin）'
  )

  assert.deepEqual(
    buildPermissionPayload({
      subject_type: 'user',
      subject_id: ' zhangsan ',
      can_view: true,
      can_search: true,
      can_upload: undefined,
      can_download: false,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }),
    {
      subject_type: 'user',
      subject_id: 'zhangsan',
      can_view: true,
      can_search: true,
      can_upload: false,
      can_download: false,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }
  )
}

run()
```

修改 `web/package.json`，在 `scripts` 中新增：

```json
"test:permissions": "node src/components/__tests__/knowledgePermissionPanelHelpers.test.js"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval/web && pnpm test:permissions
```

预期：FAIL，报错包含：

```text
Cannot find module '../knowledgePermissionPanelHelpers.js'
```

- [ ] **步骤 3：编写最少实现代码**

创建 `web/src/components/knowledgePermissionPanelHelpers.js`：

```js
export const permissionOptions = [
  { key: 'can_view', label: '查看' },
  { key: 'can_search', label: '问答' },
  { key: 'can_upload', label: '上传' },
  { key: 'can_download', label: '下载' },
  { key: 'can_delete', label: '删除' },
  { key: 'can_manage', label: '管理' },
  { key: 'can_grant', label: '授权' },
  { key: 'can_export', label: '导出' }
]

export const permissionKeys = permissionOptions.map((item) => item.key)

export const permissionPresets = {
  readonly: {
    label: '只读',
    flags: {
      can_view: true,
      can_search: true,
      can_upload: false,
      can_download: false,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }
  },
  editor: {
    label: '编辑',
    flags: {
      can_view: true,
      can_search: true,
      can_upload: true,
      can_download: true,
      can_delete: false,
      can_manage: false,
      can_grant: false,
      can_export: false
    }
  },
  manager: {
    label: '管理',
    flags: {
      can_view: true,
      can_search: true,
      can_upload: true,
      can_download: true,
      can_delete: true,
      can_manage: true,
      can_grant: true,
      can_export: true
    }
  }
}

export const roleOptions = [
  { value: 'admin', label: '管理员' },
  { value: 'user', label: '普通用户' },
  { value: 'superadmin', label: '超级管理员' }
]

export const resetPermissionFlags = (flags = {}) => {
  return Object.fromEntries(permissionKeys.map((key) => [key, Boolean(flags[key])]))
}

export const buildPermissionPayload = (form) => {
  return {
    subject_type: form.subject_type,
    subject_id: String(form.subject_id || '').trim(),
    ...resetPermissionFlags(form)
  }
}

export const formatSubjectLabel = (record, { users = [], departments = [] } = {}) => {
  if (record.subject_type === 'user') {
    const user = users.find((item) => item.uid === record.subject_id)
    return user?.username ? `${user.username}（${record.subject_id}）` : record.subject_id
  }

  if (record.subject_type === 'department') {
    const department = departments.find((item) => Number(item.id) === Number(record.subject_id))
    return department?.name ? `${department.name}（${record.subject_id}）` : record.subject_id
  }

  if (record.subject_type === 'role') {
    const role = roleOptions.find((item) => item.value === record.subject_id)
    return role?.label ? `${role.label}（${record.subject_id}）` : record.subject_id
  }

  return record.subject_id
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval/web && pnpm test:permissions
```

预期：PASS，命令退出码为 0。

- [ ] **步骤 5：Commit**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval && git add web/package.json web/src/components/knowledgePermissionPanelHelpers.js web/src/components/__tests__/knowledgePermissionPanelHelpers.test.js && git commit -m "test: 补充知识库权限面板纯函数测试"
```

预期：生成一个包含测试与 helper 的提交。

---

### 任务 2：接入用户、部门、角色选择器与权限预设

**文件：**
- 修改：`web/src/components/KnowledgePermissionPanel.vue`
- 测试：`web/src/components/__tests__/knowledgePermissionPanelHelpers.test.js`

- [ ] **步骤 1：编写失败的测试**

在 `web/src/components/__tests__/knowledgePermissionPanelHelpers.test.js` 的 `run()` 中追加以下断言，放在 `buildPermissionPayload` 断言之后：

```js
  assert.equal(
    formatSubjectLabel(
      { subject_type: 'user', subject_id: 'unknown' },
      {
        users: [{ uid: 'zhangsan', username: '张三' }],
        departments: []
      }
    ),
    'unknown'
  )

  assert.equal(
    formatSubjectLabel(
      { subject_type: 'department', subject_id: '99' },
      {
        users: [],
        departments: [{ id: 10, name: '研发部' }]
      }
    ),
    '99'
  )
```

- [ ] **步骤 2：运行测试验证通过**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval/web && pnpm test:permissions
```

预期：PASS。此步骤确认 helper 已覆盖未知用户/部门 fallback 行为；如果失败，先修 helper，再继续改 Vue 组件。

- [ ] **步骤 3：修改 `KnowledgePermissionPanel.vue` 导入和状态**

将 `<script setup>` 顶部导入改为：

```js
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { databaseApi } from '@/apis/knowledge_api'
import { authApi } from '@/apis/auth_api'
import { departmentApi } from '@/apis/department_api'
import {
  buildPermissionPayload,
  formatSubjectLabel,
  permissionKeys,
  permissionOptions,
  permissionPresets,
  roleOptions
} from './knowledgePermissionPanelHelpers'
```

删除组件内原有 `permissionOptions` 和 `permissionKeys` 定义。

在 `const saving = ref(false)` 后新增：

```js
const optionLoading = ref(false)
const users = ref([])
const departments = ref([])

const userOptions = computed(() =>
  users.value.map((item) => ({
    value: item.uid,
    label: item.username ? `${item.username}（${item.uid}）` : item.uid
  }))
)

const departmentOptions = computed(() =>
  departments.value.map((item) => ({
    value: String(item.id),
    label: item.name ? `${item.name}（${item.id}）` : String(item.id)
  }))
)

const subjectOptions = computed(() => {
  if (form.subject_type === 'department') return departmentOptions.value
  if (form.subject_type === 'role') return roleOptions
  return userOptions.value
})
```

- [ ] **步骤 4：修改表单模板为选择器和预设按钮**

将对象 ID 的 `<a-input>` 表单项替换为：

```vue
<a-form-item label="授权对象">
  <a-select
    v-model:value="form.subject_id"
    show-search
    allow-clear
    :loading="optionLoading"
    :options="subjectOptions"
    style="width: 260px"
    placeholder="请选择授权对象"
    option-filter-prop="label"
  />
</a-form-item>
```

在权限复选框 `<a-form-item>` 前新增预设表单项：

```vue
<a-form-item label="权限预设">
  <a-space>
    <a-button
      v-for="preset in Object.entries(permissionPresets)"
      :key="preset[0]"
      size="small"
      @click="applyPreset(preset[0])"
    >
      {{ preset[1].label }}
    </a-button>
  </a-space>
</a-form-item>
```

- [ ] **步骤 5：新增加载选项和预设方法**

在 `subjectTypeLabel` 后新增：

```js
const subjectLabel = (record) => formatSubjectLabel(record, { users: users.value, departments: departments.value })

const loadSubjectOptions = async () => {
  optionLoading.value = true
  try {
    const [userResult, departmentResult] = await Promise.all([
      authApi.getUserAccessOptions(),
      departmentApi.getDepartments()
    ])
    users.value = Array.isArray(userResult) ? userResult : []
    departments.value = departmentResult.departments || departmentResult || []
  } catch (error) {
    message.warning(error.message || '加载授权对象失败，请手动刷新后重试')
    users.value = []
    departments.value = []
  } finally {
    optionLoading.value = false
  }
}

const applyPreset = (presetKey) => {
  const preset = permissionPresets[presetKey]
  if (!preset) return
  Object.assign(form, preset.flags)
}
```

修改 `savePermission` 中 payload 构建为：

```js
const payload = buildPermissionPayload(form)
if (!payload.subject_id) {
  message.warning('请选择授权对象')
  return
}
saving.value = true
try {
  await databaseApi.upsertPermission(props.kbId, payload)
  message.success('授权已保存')
  resetForm()
  await loadPermissions()
} catch (error) {
  message.error(error.message || '保存授权失败')
} finally {
  saving.value = false
}
```

修改 `watch(() => props.kbId, ...)` 回调为：

```js
() => {
  resetForm()
  loadPermissions()
  loadSubjectOptions()
}
```

修改 `onMounted(loadPermissions)` 为：

```js
onMounted(() => {
  loadPermissions()
  loadSubjectOptions()
})
```

新增 subject type watch：

```js
watch(
  () => form.subject_type,
  () => {
    form.subject_id = ''
  }
)
```

- [ ] **步骤 6：修改表格授权对象显示**

在 `<template #bodyCell>` 中，在 `subject_type` 分支后、`actions` 分支前新增：

```vue
<template v-else-if="column.key === 'subject_id'">
  <span>{{ subjectLabel(record) }}</span>
</template>
```

- [ ] **步骤 7：运行测试和构建验证**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval/web && pnpm test:permissions
cd /home/hmy/demo/shanli/yuxi-eval/web && pnpm build
```

预期：两个命令均 PASS，`pnpm build` 输出包含 `✓ built`。

- [ ] **步骤 8：Commit**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval && git add web/src/components/KnowledgePermissionPanel.vue web/src/components/__tests__/knowledgePermissionPanelHelpers.test.js && git commit -m "feat: 完善知识库权限编辑面板"
```

预期：生成一个包含 Vue 组件改动的提交。

---

### 任务 3：最终验证权限前端闭环

**文件：**
- 验证：`web/src/components/KnowledgePermissionPanel.vue`
- 验证：`web/src/components/knowledgePermissionPanelHelpers.js`
- 验证：`web/package.json`

- [ ] **步骤 1：运行前端权限测试**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval/web && pnpm test:permissions
```

预期：PASS，退出码为 0。

- [ ] **步骤 2：运行前端构建**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval/web && pnpm build
```

预期：PASS，输出包含：

```text
✓ built
```

- [ ] **步骤 3：检查 git diff 仅包含本轮前端权限面板相关文件**

运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval && git status --short
```

预期：若前两次 commit 已完成，本轮相关文件没有未提交改动；历史已有的后端权限改动可继续存在，不在此任务中处理。

- [ ] **步骤 4：记录手工验收路径**

在最终回复中写明以下手工验收路径：

```text
1. admin 登录 AI知识库 前端。
2. 进入任一知识库详情页。
3. 点击顶部“权限设置”或权限 tab。
4. 授权类型选择“用户”，授权对象选择一个用户，点击“只读”，保存。
5. 表格显示该用户中文名和 uid，并显示“查看/问答”为允许。
6. 点击“编辑”，表单回填该用户和权限位。
7. 授权类型切换为“部门”，对象列表切换为部门。
8. 授权类型切换为“角色”，对象列表显示管理员/普通用户/超级管理员。
9. 删除一条授权，表格刷新且授权消失。
```

- [ ] **步骤 5：Commit 验证记录（如有文档改动）**

如果执行中新增了验证文档，运行：

```bash
cd /home/hmy/demo/shanli/yuxi-eval && git add <新增的验证文档路径> && git commit -m "docs: 记录知识库权限面板验收路径"
```

如果没有新增验证文档，不执行 commit，只在最终回复中报告测试与构建结果。

---

## 自检结果

- 规格覆盖度：计划覆盖对象选择、权限预设、权限保存 payload、权限矩阵可读显示、测试与构建验证。
- 占位符扫描：无“待定”、“TODO”、“后续实现”、“类似任务”等占位内容。
- 类型一致性：统一使用 `subject_type`、`subject_id`、`permissionKeys`、`permissionPresets`、`buildPermissionPayload`、`formatSubjectLabel`。
- 范围控制：不新增后端接口，不做批量授权，不抽象通用 ACL 组件，仅完成知识库权限面板 MVP。
