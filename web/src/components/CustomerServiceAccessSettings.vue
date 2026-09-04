<!-- 客服接入设置：业务线 × 企微客服命名条目（绑定即按线转接）
     拆自 BasicSettingsSection（2026-09-04）：设置页「客服接入设置」独立页签。
     视觉重构（2026-09-04）：两块编辑面板（客服团队 / 业务线与绑定）+ 只读「转接规则总览」，
     头部自动保存状态 + 字段内联校验错误；保存语义与字段完全不变（失焦/改动即批量保存）。 -->

<template>
  <div class="cs-access-page">
    <!-- 页头：标题 + 自动保存状态 -->
    <div class="cs-page-head">
      <div class="section-title cs-page-title">{{ $t('settings.csAccessTitle') }}</div>
      <span class="cs-save" :data-state="saveState" role="status" aria-live="polite">
        <LoaderCircle v-if="saveState === 'saving'" :size="13" class="cs-spin" />
        <AlertTriangle v-else-if="saveState === 'error'" :size="13" />
        <Check v-else-if="saveState === 'saved'" :size="13" />
        <template v-if="saveState === 'saving'">{{ $t('settings.csSavingLabel') }}</template>
        <template v-else-if="saveState === 'error'">{{ $t('settings.csSaveErrorLabel') }}</template>
        <template v-else-if="saveState === 'saved'">
          {{ $t('settings.csSavedLabel', { time: lastSavedAt }) }}
        </template>
        <template v-else>{{ $t('settings.csAutoSaveHint') }}</template>
      </span>
    </div>
    <p class="section-description cs-page-desc">{{ $t('settings.csAccessDesc') }}</p>

    <!-- 面板一：客服团队（命名条目，每条可含 1..N 个企微入口 URL） -->
    <section class="cs-panel">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><Headphones :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.csServicesTitle') }}</h3>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.csServicesDesc') }}</p>
      </header>

      <div class="cs-panel-body">
        <div v-if="csEntries.length === 0" class="cs-empty">{{ $t('settings.csServicesEmpty') }}</div>
        <div
          v-else
          v-for="(entry, index) in csEntries"
          :key="entry.id"
          class="cs-row"
          :class="{ 'cs-row-invalid': rowErrOn('service', index, ['name', 'urls']) }"
        >
          <div class="cs-row-grid cs-service-grid">
            <span class="cs-index">{{ indexLabel(index) }}</span>
            <div class="cs-field" :class="{ 'has-error': errOn('service', index, 'name') }">
              <label class="cs-label">{{ $t('settings.csServiceNameLabel') }}</label>
              <a-input
                v-model:value="entry.name"
                :placeholder="$t('settings.csServiceNamePlaceholder')"
                @input="clearError"
                @blur="flushAccessSettings"
              />
              <div v-if="errOf('service', index, 'name')" class="cs-error">
                {{ errOf('service', index, 'name') }}
              </div>
            </div>
            <div class="cs-field" :class="{ 'has-error': errOn('service', index, 'urls') }">
              <label class="cs-label">{{ $t('settings.csServiceUrlsLabel') }}</label>
              <a-textarea
                v-model:value="entry.urlsText"
                :placeholder="$t('settings.csServiceUrlsPlaceholder')"
                :auto-size="{ minRows: 1, maxRows: 4 }"
                @input="clearError"
                @blur="flushAccessSettings"
              />
              <div v-if="errOf('service', index, 'urls')" class="cs-error">
                {{ errOf('service', index, 'urls') }}
              </div>
            </div>
            <button
              type="button"
              class="cs-remove"
              :aria-label="$t('settings.csRemoveService')"
              :title="$t('settings.csRemoveService')"
              @click="removeServiceEntry(index)"
            >
              <Trash2 :size="15" />
            </button>
          </div>
        </div>
        <a-button type="dashed" block @click="addServiceEntry">
          <template #icon><Plus :size="15" /></template>
          {{ $t('settings.csAddService') }}
        </a-button>
      </div>
    </section>

    <!-- 面板二：业务线（拒答分类 + 绑定客服即按线转接） -->
    <section class="cs-panel">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><GitBranch :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.csLinesTitle') }}</h3>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.csLinesDesc') }}</p>
      </header>

      <div class="cs-panel-body">
        <div v-if="businessLines.length === 0" class="cs-empty">
          {{ $t('settings.csLinesEmpty') }}
        </div>
        <div
          v-else
          v-for="row in businessLines"
          :key="row.clientKey"
          class="cs-row cs-line-row"
          :class="{ 'cs-row-invalid': rowErrOn('line', rowIndex(row), ['code', 'name']) }"
        >
          <div class="cs-line-fields">
            <span class="cs-index">{{ indexLabel(rowIndex(row)) }}</span>
            <div class="cs-field" :class="{ 'has-error': errOn('line', rowIndex(row), 'code') }">
              <label class="cs-label">{{ $t('settings.businessLineCodeLabel') }}</label>
              <a-input
                v-model:value="row.code"
                :placeholder="$t('settings.businessLineCodePlaceholder')"
                @input="clearError"
                @blur="flushAccessSettings"
              />
              <div v-if="errOf('line', rowIndex(row), 'code')" class="cs-error">
                {{ errOf('line', rowIndex(row), 'code') }}
              </div>
            </div>
            <div class="cs-field" :class="{ 'has-error': errOn('line', rowIndex(row), 'name') }">
              <label class="cs-label">{{ $t('settings.businessLineNameLabel') }}</label>
              <a-input
                v-model:value="row.name"
                :placeholder="$t('settings.businessLineNamePlaceholder')"
                @input="clearError"
                @blur="flushAccessSettings"
              />
              <div v-if="errOf('line', rowIndex(row), 'name')" class="cs-error">
                {{ errOf('line', rowIndex(row), 'name') }}
              </div>
            </div>
            <div class="cs-field">
              <label class="cs-label">{{ $t('settings.businessLineKeywordsLabel') }}</label>
              <a-input
                v-model:value="row.keywords"
                :placeholder="$t('settings.businessLineKeywordsPlaceholder')"
                @input="clearError"
                @blur="flushAccessSettings"
              />
            </div>
            <button
              type="button"
              class="cs-remove"
              :aria-label="$t('settings.removeBusinessLine')"
              :title="$t('settings.removeBusinessLine')"
              @click="removeBusinessLine(row)"
            >
              <Trash2 :size="15" />
            </button>
          </div>
          <div class="cs-field cs-binding">
            <label class="cs-label">{{ $t('settings.csLineBindingLabel') }}</label>
            <a-select
              v-model:value="row.boundIds"
              mode="multiple"
              :options="csServiceOptions"
              :placeholder="$t('settings.csLineBindingPlaceholder')"
              @change="flushAccessSettings"
            />
          </div>
        </div>
        <a-button type="dashed" block @click="addBusinessLine">
          <template #icon><Plus :size="15" /></template>
          {{ $t('settings.addBusinessLine') }}
        </a-button>
      </div>
    </section>

    <!-- 面板三：转接规则总览（只读，实时反映上方配置） -->
    <section class="cs-panel">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><Route :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.csOverviewTitle') }}</h3>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.csOverviewDesc') }}</p>
      </header>

      <div class="cs-panel-body">
        <div v-if="!hasNamedService" class="cs-empty">{{ $t('settings.csOverviewEmpty') }}</div>
        <template v-else>
          <div class="cs-route-list">
            <div v-for="r in overviewRows" :key="r.key" class="cs-route-row">
              <div class="cs-route-line" :class="{ unknown: r.isUnknown }">
                <span class="cs-line-code">{{ r.code }}</span>
                <span class="cs-line-name">
                  {{ r.isUnknown ? $t('settings.csOverviewUnknown') : r.name }}
                </span>
              </div>
              <ArrowRight class="cs-route-arrow" :size="15" />
              <div class="cs-route-target">
                <template v-if="r.direct.length">
                  <span v-for="n in r.direct" :key="n" class="cs-team-chip">{{ n }}</span>
                </template>
                <template v-else-if="generalPool.length">
                  <span class="cs-team-chip cs-team-chip-fallback">
                    {{ $t('settings.csOverviewFallbackTag') }}
                  </span>
                  <span v-for="n in generalPool" :key="n" class="cs-team-chip cs-team-chip-pool">
                    {{ n }}
                  </span>
                </template>
                <span v-else class="cs-nopool">{{ $t('settings.csOverviewNoPool') }}</span>
              </div>
            </div>
          </div>
          <p class="cs-panel-tip">
            <Info :size="14" />
            <span>{{ $t('settings.csUnboundTip') }}</span>
          </p>
        </template>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import {
  AlertTriangle,
  ArrowRight,
  Check,
  GitBranch,
  Headphones,
  Info,
  LoaderCircle,
  Plus,
  Route,
  Trash2
} from 'lucide-vue-next'

const { t } = useI18n()
const configStore = useConfigStore()

// ---- 客服接入设置：企微客服命名条目 + 业务线绑定（绑定即按线转接） ----
// 条目 {id,name,urls[]}，业务线行可选绑定的客服 id 数组。两个子编辑器共享一次批量保存
// （setConfigValues 同时提交两键），后端原子校验保证引用完整；任何一处编辑都 flush 当前
// 完整两数组，避免「先存线、后存客服」时序产生脏绑定。校验失败时以红框 + 行内错误提示
// 呈现（不再用全局 toast），其余输入即时保存。
const isHttpsUrl = (url) => {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:'
  } catch {
    return false
  }
}

const splitServiceUrls = (text) => (text || '').split(/[\n,，]+/).map((s) => s.trim()).filter(Boolean)

// 新增客服条目时前端预生成 id，保证「绑定下拉」在保存前即可引用；后端仅兜底补缺失 id。
// 业务线行另有稳定的 clientKey 作 v-for key，避免「删除中间行 + index key」导致的输入串值。
let seq = 0
const genId = (prefix) => `${prefix}-${Date.now().toString(36)}-${(seq++).toString(36)}`

const saveState = ref('idle') // idle | saving | saved | error
const lastSavedAt = ref('')
const fieldError = ref(null) // { kind: 'service'|'line', index, field, message }，同一时刻只提示一处

const fmtTime = () => {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const clearError = () => {
  fieldError.value = null
}
const errOf = (kind, index, field) => {
  const e = fieldError.value
  return e && e.kind === kind && e.index === index && e.field === field ? e.message : ''
}
const errOn = (kind, index, field) => errOf(kind, index, field) !== ''
const rowErrOn = (kind, index, fields) => fields.some((field) => errOn(kind, index, field))

const indexLabel = (index) => String(index + 1).padStart(2, '0')

// ---- 客服条目编辑器 ----
const servicesDirty = ref(false)
const csEntries = ref([])

// 配置异步加载时回填一次；用户在编辑中（servicesDirty）不覆盖。
watch(
  () => configStore.config?.wecom_customer_services,
  (entries) => {
    if (servicesDirty.value) return
    csEntries.value = (Array.isArray(entries) ? entries : []).map((entry) => ({
      id: entry.id || genId('cs'),
      name: entry.name || '',
      urlsText: (Array.isArray(entry.urls) ? entry.urls : []).join('\n')
    }))
  },
  { immediate: true }
)

const csServiceOptions = computed(() =>
  csEntries.value
    .filter((entry) => (entry.name || '').trim())
    .map((entry) => ({ value: entry.id, label: entry.name.trim() }))
)

const addServiceEntry = () => {
  csEntries.value.push({ id: genId('cs'), name: '', urlsText: '' })
  servicesDirty.value = true
}

const removeServiceEntry = (index) => {
  const removed = csEntries.value.splice(index, 1)[0]
  if (removed) {
    // 删除客服条目同时解除业务线对它的绑定，避免脏引用被后端跨字段校验拦截。
    for (const row of businessLines.value) {
      const pos = (row.boundIds || []).indexOf(removed.id)
      if (pos !== -1) row.boundIds.splice(pos, 1)
    }
  }
  flushAccessSettings()
}

// 校验并序列化客服条目（新增未填的空行跳过）；非法返回 { error }。
const buildServicesPayload = () => {
  const services = []
  for (let index = 0; index < csEntries.value.length; index++) {
    const entry = csEntries.value[index]
    const name = (entry.name || '').trim()
    const urls = splitServiceUrls(entry.urlsText)
    if (!name && urls.length === 0) continue
    const fail = (field, key) => ({ error: { kind: 'service', index, field, message: t(key) } })
    if (!name) return fail('name', 'settings.csServiceNameRequired')
    if (name.length > 40) return fail('name', 'settings.csServiceNameTooLong')
    if (urls.length === 0) return fail('urls', 'settings.csServiceNeedUrl')
    for (const url of urls) {
      if (!isHttpsUrl(url)) return fail('urls', 'settings.csServiceInvalidUrl')
    }
    services.push({ id: entry.id || genId('cs'), name, urls })
  }
  return { services }
}

// ---- 业务线（拒答分类标签）编辑器：设置页可维护，作为拒答 domain 标签可选值 ----
// code 校验与服务端一致（小写 snake_case、≤32、unknown 系统保留、清单内唯一）；服务端吞错，
// 故用户可输入的常见非法值在前端拦截并提示。
const BUSINESS_LINE_CODE_PATTERN = /^[a-z][a-z0-9_]{0,31}$/

// 与服务端拆分规则一致：支持中英文逗号/顿号/空白分隔。
const splitKeywords = (text) => (text || '').split(/[,，、/\s]+/).map((s) => s.trim()).filter(Boolean)

const businessLinesDirty = ref(false)
const businessLines = ref([])

// 配置异步加载时回填一次；用户在编辑中（businessLinesDirty）不覆盖。
// keywords 兼容「数组」与「字符串」（历史版本曾按字符串乐观提交）两种形态；
// customer_service_ids 映射为本行「绑定客服」多选的本地数组。
watch(
  () => configStore.config?.business_lines,
  (lines) => {
    if (businessLinesDirty.value) return
    businessLines.value = (Array.isArray(lines) ? lines : []).map((row) => ({
      clientKey: genId('bl'),
      code: row.code || '',
      name: row.name || '',
      keywords: typeof row.keywords === 'string'
        ? row.keywords
        : (Array.isArray(row.keywords) ? row.keywords : []).join('，'),
      boundIds: Array.isArray(row.customer_service_ids) ? [...row.customer_service_ids] : []
    }))
  },
  { immediate: true }
)

const rowIndex = (row) => businessLines.value.indexOf(row)

const addBusinessLine = () => {
  businessLines.value.push({ clientKey: genId('bl'), code: '', name: '', keywords: '', boundIds: [] })
  businessLinesDirty.value = true
}

const removeBusinessLine = (row) => {
  businessLines.value.splice(businessLines.value.indexOf(row), 1)
  flushAccessSettings()
}

// 校验并序列化业务线；非法返回 { error }。customer_service_ids 仅在有绑定时带上（与存储语义一致）。
const buildBusinessLinesPayload = () => {
  const rows = []
  const seenCodes = new Set()
  for (let index = 0; index < businessLines.value.length; index++) {
    const row = businessLines.value[index]
    const code = (row.code || '').trim().toLowerCase()
    const name = (row.name || '').trim()
    const fail = (field, key, params) => ({
      error: { kind: 'line', index, field, message: t(key, params) }
    })
    if (!code && !name) continue // 完全空行（新增未填）不提交
    if (!BUSINESS_LINE_CODE_PATTERN.test(code)) return fail('code', 'settings.businessLineInvalidCode')
    if (code === 'unknown') return fail('code', 'settings.businessLineCodeReserved')
    if (seenCodes.has(code)) return fail('code', 'settings.businessLineDuplicateCode', { code })
    if (!name) return fail('name', 'settings.businessLineInvalidName')
    seenCodes.add(code)
    rows.push({
      code,
      name,
      keywords: splitKeywords(row.keywords),
      ...(row.boundIds && row.boundIds.length ? { customer_service_ids: [...row.boundIds] } : {})
    })
  }
  return { rows }
}

const flushAccessSettings = async () => {
  const servicesBuilt = buildServicesPayload()
  if (servicesBuilt.error) return flushFailed(servicesBuilt.error)
  const linesBuilt = buildBusinessLinesPayload()
  if (linesBuilt.error) return flushFailed(linesBuilt.error)
  fieldError.value = null
  servicesDirty.value = false
  businessLinesDirty.value = false
  saveState.value = 'saving'
  const { ok } = await configStore.setConfigValues({
    wecom_customer_services: servicesBuilt.services,
    business_lines: linesBuilt.rows
  })
  saveState.value = ok ? 'saved' : 'error'
  if (ok) lastSavedAt.value = fmtTime()
}

const flushFailed = (error) => {
  fieldError.value = error
  servicesDirty.value = true
  businessLinesDirty.value = true
  saveState.value = 'error'
}

// ---- 转接规则总览（只读）：镜像后端兜底链，给管理员「当前配置实际转给谁」的实时预览 ----
// 已绑定线 → 所绑客服（轮替）；未绑定线及无法归类的 unknown → 通用客服兜底：
//   优先「通用客服(kefu)」线的绑定，其次取未被任何业务线绑定的客服条目（默认池）。
const serviceNameById = computed(() => {
  const map = {}
  for (const entry of csEntries.value) {
    const name = (entry.name || '').trim()
    if (name) map[entry.id] = name
  }
  return map
})
const hasNamedService = computed(() => Object.keys(serviceNameById.value).length > 0)

const resolveNames = (ids) => {
  const names = []
  for (const id of ids || []) {
    const name = serviceNameById.value[id]
    if (name) names.push(name)
  }
  return names
}

const generalPool = computed(() => {
  const kefu = businessLines.value.find((row) => (row.code || '').trim().toLowerCase() === 'kefu')
  if (kefu && (kefu.boundIds || []).length) return resolveNames(kefu.boundIds)
  const bound = new Set()
  for (const row of businessLines.value) for (const id of row.boundIds || []) bound.add(id)
  return csEntries.value
    .filter((entry) => (entry.name || '').trim() && !bound.has(entry.id))
    .map((entry) => entry.name.trim())
})

const overviewRows = computed(() => {
  const rows = businessLines.value
    .filter((row) => (row.code || '').trim() && (row.name || '').trim())
    .map((row) => ({
      key: row.clientKey,
      code: (row.code || '').trim(),
      name: (row.name || '').trim(),
      direct: resolveNames(row.boundIds)
    }))
  rows.push({ key: 'unknown', code: 'unknown', name: '', direct: [], isUnknown: true })
  return rows
})
</script>

<style lang="less" scoped>
.cs-access-page {
  .cs-page-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin: 6px 0 0;

    .cs-page-title {
      margin: 0;
    }
  }

  .cs-page-desc {
    margin: 4px 0 16px;
  }

  .cs-save {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    border: 1px solid transparent;
    font-size: 12px;
    font-weight: 500;
    line-height: 1;
    white-space: nowrap;
    transition: all 0.15s;

    &[data-state='idle'],
    &[data-state='saving'] {
      background: var(--gray-50);
      border-color: var(--gray-200);
      color: var(--gray-600);
    }
    &[data-state='saved'] {
      background: var(--color-success-50);
      border-color: var(--color-success-100);
      color: var(--color-success-700);
    }
    &[data-state='error'] {
      background: var(--color-error-50);
      border-color: var(--color-error-100);
      color: var(--color-error-700);
    }

    svg {
      flex-shrink: 0;
    }
  }

  .cs-spin {
    animation: cs-spin 0.9s linear infinite;
  }
  @keyframes cs-spin {
    to {
      transform: rotate(360deg);
    }
  }

  .cs-panel {
    background: var(--gray-50);
    border: 1px solid var(--gray-200);
    border-radius: 10px;
    overflow: hidden;

    + .cs-panel {
      margin-top: 16px;
    }
  }

  .cs-panel-head {
    padding: 12px 16px;
    border-bottom: 1px solid var(--gray-200);

    .cs-panel-head-title {
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .cs-panel-icon {
      width: 26px;
      height: 26px;
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 8px;
      background: var(--main-10);
      color: var(--main-700);
    }

    .cs-panel-title {
      margin: 0;
      font-size: 15px;
      font-weight: 600;
      color: var(--gray-900);
    }

    .cs-panel-desc {
      margin: 6px 0 0;
      font-size: 13px;
      line-height: 1.5;
      color: var(--gray-500);
    }
  }

  .cs-panel-body {
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .cs-row {
    background: var(--gray-0);
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    padding: 10px 12px;
    transition: border-color 0.15s;

    &:hover {
      border-color: var(--gray-200);
    }

    &.cs-row-invalid {
      border-color: var(--color-error-500);
    }
  }

  .cs-row-grid {
    display: grid;
    gap: 8px 12px;
    align-items: flex-end;
  }

  .cs-service-grid {
    grid-template-columns: auto minmax(0, 1fr) minmax(0, 1.5fr) auto;
  }

  .cs-line-fields {
    display: grid;
    gap: 8px 12px;
    align-items: flex-end;
    grid-template-columns: auto minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.2fr) auto;
  }

  .cs-index {
    align-self: center;
    padding-bottom: 2px;
    font-size: 12px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    color: var(--gray-400);
  }

  .cs-field {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;

    .cs-label {
      font-size: 13px;
      font-weight: 500;
      color: var(--gray-700);
    }

    :deep(.ant-input),
    :deep(.ant-input-affix-wrapper),
    :deep(.ant-select),
    :deep(.ant-input-number) {
      width: 100%;
    }

    &.has-error {
      :deep(.ant-input),
      :deep(.ant-input-affix-wrapper),
      :deep(.ant-select-selector) {
        border-color: var(--color-error-500);
      }
      :deep(.ant-input:focus),
      :deep(.ant-input-affix-wrapper:focus-within) {
        box-shadow: 0 0 0 2px var(--color-error-100);
      }
    }
  }

  .cs-error {
    font-size: 12px;
    line-height: 1.4;
    color: var(--color-error-700);
  }

  .cs-binding {
    margin-top: 2px;
  }

  .cs-remove {
    align-self: center;
    flex-shrink: 0;
    width: 28px;
    height: 28px;
    border: none;
    border-radius: 8px;
    background: transparent;
    color: var(--gray-500);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all 0.15s;

    &:hover {
      background: var(--color-error-50);
      color: var(--color-error-700);
    }
  }

  .cs-empty {
    padding: 16px;
    text-align: center;
    font-size: 13px;
    line-height: 1.5;
    color: var(--gray-500);
    background: var(--gray-0);
    border: 1px dashed var(--gray-200);
    border-radius: 8px;
  }

  /* —— 转接规则总览 —— */
  .cs-route-list {
    background: var(--gray-0);
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    overflow: hidden;
  }

  .cs-route-row {
    display: grid;
    grid-template-columns: minmax(150px, 240px) auto 1fr;
    gap: 10px 14px;
    align-items: center;
    padding: 9px 12px;

    + .cs-route-row {
      border-top: 1px solid var(--gray-100);
    }
  }

  .cs-route-line {
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 8px;

    .cs-line-code {
      flex-shrink: 0;
      max-width: 110px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 12px;
      font-weight: 600;
      color: var(--gray-800);
      background: var(--gray-100);
      border-radius: 6px;
      padding: 2px 8px;
    }

    .cs-line-name {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 13px;
      color: var(--gray-600);
    }

    &.unknown {
      .cs-line-code {
        background: transparent;
        border: 1px dashed var(--gray-200);
        color: var(--gray-500);
      }
      .cs-line-name {
        color: var(--gray-500);
      }
    }
  }

  .cs-route-arrow {
    flex-shrink: 0;
    color: var(--gray-400);
  }

  .cs-route-target {
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .cs-team-chip {
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 12px;
    font-weight: 500;
    padding: 3px 10px;
    border-radius: 999px;
    background: var(--main-10);
    color: var(--main-700);

    &.cs-team-chip-fallback,
    &.cs-team-chip-pool {
      background: var(--color-warning-50);
      color: var(--color-warning-700);
    }
  }

  .cs-nopool {
    font-size: 12px;
    line-height: 1.5;
    color: var(--color-error-700);
  }

  .cs-panel-tip {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--gray-500);

    svg {
      flex-shrink: 0;
      margin-top: 1px;
      color: var(--gray-400);
    }
  }

  @media (max-width: 900px) {
    .cs-save {
      white-space: normal;
      text-align: right;
    }
  }

  @media (max-width: 768px) {
    .cs-page-head {
      align-items: flex-start;
      flex-direction: column;
      gap: 8px;

      .cs-save {
        text-align: left;
      }
    }

    .cs-service-grid,
    .cs-line-fields {
      grid-template-columns: 1fr;
    }

    .cs-index {
      display: none;
    }

    .cs-remove {
      justify-self: start;
    }
  }

  @media (max-width: 560px) {
    .cs-route-row {
      grid-template-columns: 1fr;
      gap: 6px;
    }

    .cs-route-arrow {
      transform: rotate(90deg);
    }
  }
}
</style>
