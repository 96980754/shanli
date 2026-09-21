<!-- 外部服务凭证设置：三项凭证在设置页自助配置，设置页有值优先、留空回退服务器 .env。
     表单只预填设置页自己的快照（configStore.config），生效值另起只读面板——
     若拿生效值预填，用户不动输入框直接保存就会把 .env 的值写成设置页的值，
     此后改 .env 重启静默失效，而面板还显示「来自设置页」，是最难排查的一种故障。 -->

<template>
  <div class="external-service-page">
    <div class="cs-page-head">
      <div class="section-title cs-page-title">{{ $t('settings.integrationsTitle') }}</div>
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
    <p class="section-description cs-page-desc">{{ $t('settings.integrationsDesc') }}</p>

    <section class="cs-panel" v-if="status">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><Activity :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.integrationsEffectiveTitle') }}</h3>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.integrationsEffectiveDesc') }}</p>
      </header>

      <div class="cs-panel-body">
        <div v-for="row in effectiveRows" :key="row.key" class="ext-effective-row">
          <span class="ext-effective-label">{{ row.label }}</span>
          <span class="ext-effective-value">{{ row.value }}</span>
          <a-tag :color="SOURCE_TAG_COLOR[row.source]">
            {{ $t(SOURCE_LABEL_KEY[row.source]) }}
          </a-tag>
        </div>
      </div>
    </section>

    <section class="cs-panel">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><Plug :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.integrationsFormTitle') }}</h3>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.integrationsFormDesc') }}</p>
      </header>

      <div class="cs-panel-body">
        <div v-for="item in FIELDS" :key="item.field" class="ext-field">
          <label class="cs-label">{{ $t(item.labelKey) }}</label>
          <a-input
            v-model:value="form[item.field]"
            :placeholder="$t(item.placeholderKey)"
            @blur="flush(item.field)"
          />
          <span class="ext-hint">{{ $t(item.hintKey) }}</span>
        </div>

        <a-alert
          type="info"
          show-icon
          class="ext-restart-alert"
          :message="$t('settings.integrationsRestartTitle')"
          :description="$t('settings.integrationsRestartDesc')"
        />
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { Activity, AlertTriangle, Check, LoaderCircle, Plug } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { integrationApi } from '@/apis/system_api'

const { t } = useI18n()
const configStore = useConfigStore()

const FIELDS = [
  {
    field: 'tavily_api_key',
    labelKey: 'settings.integrationsTavilyLabel',
    placeholderKey: 'settings.integrationsTavilyPlaceholder',
    hintKey: 'settings.integrationsTavilyHint'
  },
  {
    field: 'paddleocr_api_token',
    labelKey: 'settings.integrationsPaddleTokenLabel',
    placeholderKey: 'settings.integrationsPaddleTokenPlaceholder',
    hintKey: 'settings.integrationsPaddleTokenHint'
  },
  {
    field: 'paddleocr_api_url',
    labelKey: 'settings.integrationsPaddleUrlLabel',
    placeholderKey: 'settings.integrationsPaddleUrlPlaceholder',
    hintKey: 'settings.integrationsPaddleUrlHint'
  }
]

// 生效值来源标注：与 Udesk 同一套取值（settings/env/unset），故复用其标签文案
const SOURCE_TAG_COLOR = { settings: 'blue', env: 'orange', unset: 'default' }
const SOURCE_LABEL_KEY = {
  settings: 'settings.udeskSourceSettings',
  env: 'settings.udeskSourceEnv',
  unset: 'settings.udeskSourceUnset'
}

const status = ref(null)

// 只读展示服务器实际生效值：设置页表单只反映设置页快照，与 .env 合并后的结果需单独查
async function loadStatus() {
  try {
    status.value = await integrationApi.getStatus()
  } catch {
    status.value = null // 无权限或接口异常时不展示该面板，不影响表单编辑
  }
}

onMounted(loadStatus)

const effectiveRows = computed(() => {
  const items = status.value?.items
  if (!items) return []
  return FIELDS.map((item) => {
    const entry = items[item.field] || {}
    return {
      key: item.field,
      label: t(item.labelKey),
      value: entry.value || '—',
      source: entry.source
    }
  })
})

const form = reactive({ tavily_api_key: '', paddleocr_api_token: '', paddleocr_api_url: '' })

const saveState = ref('')
const lastSavedAt = ref('')
const loaded = ref(false)

// 配置到达后回填表单（仅一次；之后以用户编辑为准，保存走显式 flush）
function fillForm(configData) {
  for (const { field } of FIELDS) {
    form[field] = configData[field] || ''
  }
  loaded.value = true
}

if (configStore.config && configStore.config.tavily_api_key !== undefined) {
  fillForm(configStore.config)
}

// 逐字段保存：只提交刚编辑的那一项，不把其它字段的快照值回写一遍
const flush = async (field) => {
  if (!loaded.value) return
  saveState.value = 'saving'
  const { ok } = await configStore.setConfigValues({ [field]: form[field] })
  saveState.value = ok ? 'saved' : 'error'
  if (ok) {
    lastSavedAt.value = dayjs().format('HH:mm:ss')
    await loadStatus() // 保存后同步刷新生效值面板
  }
}
</script>

<style scoped lang="less">
.external-service-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.cs-page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.cs-page-title { margin: 0; font-size: 18px; color: var(--gray-1000); }
.cs-page-desc { margin: 4px 0 0; color: var(--gray-600); font-size: 13px; }
.cs-save { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--gray-500); }
.cs-spin { animation: ext-spin 1s linear infinite; }
@keyframes ext-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

.cs-panel {
  border: 1px solid var(--gray-150);
  border-radius: 10px;
  background: var(--gray-0);
  padding: 16px 20px;
}
.cs-panel-head-title {
  display: flex;
  align-items: center;
  gap: 8px;
  .cs-panel-icon { display: inline-flex; color: var(--primary-color, var(--gray-600)); }
  .cs-panel-title { margin: 0; font-size: 15px; color: var(--gray-900); }
}
.cs-panel-desc { margin: 4px 0 12px; color: var(--gray-500); font-size: 12px; }

.ext-field {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  .cs-label { flex: none; width: 150px; text-align: right; color: var(--gray-700); }
  .ext-hint { color: var(--gray-500); font-size: 12px; }
  :deep(.ant-input) { width: 320px; }
}
.ext-restart-alert { margin-top: 8px; }

.ext-effective-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--gray-100);

  &:last-child { border-bottom: none; }

  .ext-effective-label { flex: none; width: 150px; text-align: right; color: var(--gray-700); }
  .ext-effective-value {
    flex: 1;
    min-width: 0;
    color: var(--gray-900);
    word-break: break-all;
  }
}
</style>
