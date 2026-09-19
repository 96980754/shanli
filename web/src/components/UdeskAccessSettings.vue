<!-- Udesk 对接设置：全部参数（含永久 Token）在设置页自助配置、保存即热同步生效。
     Token 是只写字段——后端落盘但读取接口一律剔除明文，故本页只有输入框、不回显，
     占位符按 describe() 的 token_configured 提示是否已配置；留空表示不修改。 -->

<template>
  <div class="udesk-access-page">
    <div class="cs-page-head">
      <div class="section-title cs-page-title">{{ $t('settings.udeskTitle') }}</div>
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
    <p class="section-description cs-page-desc">{{ $t('settings.udeskDesc') }}</p>

    <section class="cs-panel" v-if="status">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><Activity :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.udeskEffectiveTitle') }}</h3>
          <a-tag :color="status.ready ? 'green' : 'default'">
            {{ status.ready ? $t('settings.udeskEffectiveReady') : $t('settings.udeskEffectiveNotReady') }}
          </a-tag>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.udeskEffectiveDesc') }}</p>
      </header>

      <div class="cs-panel-body">
        <div v-for="row in effectiveRows" :key="row.key" class="udesk-effective-row">
          <span class="udesk-effective-label">{{ row.label }}</span>
          <span class="udesk-effective-value">{{ row.value }}</span>
          <a-tag :color="SOURCE_TAG_COLOR[row.source]">
            {{ $t(SOURCE_LABEL_KEY[row.source]) }}
          </a-tag>
        </div>
      </div>
    </section>

    <section class="cs-panel">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><Headset :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.udeskConnectionTitle') }}</h3>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.udeskConnectionDesc') }}</p>
      </header>

      <div class="cs-panel-body">
        <div class="udesk-field">
          <label class="cs-label">{{ $t('settings.udeskEnabledLabel') }}</label>
          <a-switch v-model:checked="form.udesk_enabled" @change="onEnabledChange" />
          <span class="udesk-hint">{{ $t('settings.udeskEnabledHint') }}</span>
        </div>

        <div class="udesk-field">
          <label class="cs-label">{{ $t('settings.udeskSubdomainLabel') }}</label>
          <a-input
            v-model:value="form.udesk_subdomain"
            :placeholder="$t('settings.udeskSubdomainPlaceholder')"
            @blur="flush"
          />
        </div>

        <div class="udesk-field">
          <label class="cs-label">{{ $t('settings.udeskEmailLabel') }}</label>
          <a-input
            v-model:value="form.udesk_email"
            :placeholder="$t('settings.udeskEmailPlaceholder')"
            @blur="flush"
          />
        </div>

        <div class="udesk-field">
          <label class="cs-label">{{ $t('settings.udeskTokenLabel') }}</label>
          <a-input-password
            v-model:value="tokenInput"
            :placeholder="
              status?.token_configured
                ? $t('settings.udeskTokenPlaceholderSet')
                : $t('settings.udeskTokenPlaceholderEmpty')
            "
            @blur="flush"
          />
        </div>

        <a-alert
          type="info"
          show-icon
          class="udesk-token-alert"
          :message="$t('settings.udeskTokenAlertTitle')"
          :description="$t('settings.udeskTokenAlertDesc')"
        />
      </div>
    </section>

    <section class="cs-panel">
      <header class="cs-panel-head">
        <div class="cs-panel-head-title">
          <span class="cs-panel-icon"><RefreshCw :size="15" /></span>
          <h3 class="cs-panel-title">{{ $t('settings.udeskSyncTitle') }}</h3>
        </div>
        <p class="cs-panel-desc">{{ $t('settings.udeskSyncDesc') }}</p>
      </header>

      <div class="cs-panel-body udesk-sync-grid">
        <div class="udesk-field">
          <label class="cs-label">{{ $t('settings.udeskOverlapLabel') }}</label>
          <a-input-number
            v-model:value="form.udesk_sync_overlap_minutes"
            :min="1"
            :max="1440"
            @change="flush"
          />
          <span class="udesk-hint">{{ $t('settings.udeskOverlapHint') }}</span>
        </div>
        <div class="udesk-field">
          <label class="cs-label">{{ $t('settings.udeskBackfillLabel') }}</label>
          <a-input-number
            v-model:value="form.udesk_backfill_start_days"
            :min="1"
            :max="30"
            @change="flush"
          />
          <span class="udesk-hint">{{ $t('settings.udeskBackfillHint') }}</span>
        </div>
        <div v-if="nextPullRunAt" class="udesk-field">
          <label class="cs-label">{{ $t('settings.udeskNextRunLabel') }}</label>
          <span class="udesk-hint">{{ $t('settings.udeskNextRunHint', { time: nextPullRunAt }) }}</span>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import dayjs from 'dayjs'
import { Activity, AlertTriangle, Check, Headset, LoaderCircle, RefreshCw } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { useConfigStore } from '@/stores/config'
import { dashboardApi } from '@/apis/dashboard_api'
import { formatFullDateTime } from '@/utils/time'

const { t } = useI18n()
const configStore = useConfigStore()

// 生效值来源标注（后端 describe() 的 sources 值 → 标签色/文案）
const SOURCE_TAG_COLOR = { settings: 'blue', env: 'orange', unset: 'default' }
const SOURCE_LABEL_KEY = {
  settings: 'settings.udeskSourceSettings',
  env: 'settings.udeskSourceEnv',
  unset: 'settings.udeskSourceUnset'
}

const status = ref(null)

// 下次自动拉取：凭证齐备时后端按 cron 时刻算好返回；未配置时为空、整行不展示
const nextPullRunAt = computed(() => {
  const value = status.value?.pull?.next_run_at
  return value ? formatFullDateTime(value) : ''
})

// 只读展示服务器实际生效值：设置页表单只反映设置页快照，与 .env 合并后的结果需单独查
async function loadStatus() {
  try {
    status.value = await dashboardApi.getUdeskStatus()
  } catch {
    status.value = null // 无权限或接口异常时不展示该面板，不影响表单编辑
  }
}

onMounted(loadStatus)

const effectiveRows = computed(() => {
  const data = status.value
  if (!data) return []
  const sources = data.sources || {}
  const dash = '—'
  return [
    {
      key: 'enabled',
      label: t('settings.udeskEnabledLabel'),
      value: data.enabled ? t('settings.udeskEffectiveOn') : t('settings.udeskEffectiveOff'),
      source: sources.enabled
    },
    {
      key: 'subdomain',
      label: t('settings.udeskSubdomainLabel'),
      value: data.subdomain || dash,
      source: sources.subdomain
    },
    { key: 'email', label: t('settings.udeskEmailLabel'), value: data.email || dash, source: sources.email },
    {
      key: 'token',
      label: t('settings.udeskTokenLabel'),
      value: data.token_configured
        ? t('settings.udeskTokenConfigured')
        : t('settings.udeskTokenNotConfigured'),
      source: sources.open_api_token
    },
    {
      key: 'overlap',
      label: t('settings.udeskOverlapLabel'),
      value: String(data.sync_overlap_minutes),
      source: sources.sync_overlap_minutes
    },
    {
      key: 'backfill',
      label: t('settings.udeskBackfillLabel'),
      value: String(data.backfill_start_days),
      source: sources.backfill_start_days
    }
  ]
})

const form = reactive({
  udesk_enabled: false,
  udesk_subdomain: '',
  udesk_email: '',
  udesk_sync_overlap_minutes: 10,
  udesk_backfill_start_days: 30
})

const saveState = ref('')
const lastSavedAt = ref('')
const loaded = ref(false)

// Token 只写不读：后端不回传明文，输入框每次加载必为空，仅在用户填了新值时才随载荷提交。
// 保存在组件里（不进 form）是为了让「留空=不修改」与其它字段的显式取值语义分开。
const tokenInput = ref('')

// 配置到达后回填表单（仅一次；之后以用户编辑为准，保存走显式 flush）
function fillForm(configData) {
  form.udesk_enabled = Boolean(configData.udesk_enabled)
  form.udesk_subdomain = configData.udesk_subdomain || ''
  form.udesk_email = configData.udesk_email || ''
  form.udesk_sync_overlap_minutes = Number(configData.udesk_sync_overlap_minutes) || 10
  form.udesk_backfill_start_days = Number(configData.udesk_backfill_start_days) || 30
  loaded.value = true
}

if (configStore.config && configStore.config.udesk_enabled !== undefined) {
  fillForm(configStore.config)
}

// 只提交本页可见字段 + 用户新填的 token（留空则不带该键，避免把已配的值清掉）。
// 开关未被用户操作过时不提交：避免改其它字段时把「跟随 .env」的状态覆盖成显式 false。
let enabledTouched = false
const onEnabledChange = () => {
  enabledTouched = true
  flush()
}

const flush = async () => {
  if (!loaded.value) return
  saveState.value = 'saving'
  const payload = { ...form }
  if (!enabledTouched) delete payload.udesk_enabled
  const token = tokenInput.value.trim()
  if (token) payload.udesk_open_api_token = token
  const { ok } = await configStore.setConfigValues(payload)
  saveState.value = ok ? 'saved' : 'error'
  if (ok) {
    tokenInput.value = '' // 保存成功即清空：明文不回显，失败则保留待重试
    lastSavedAt.value = dayjs().format('HH:mm:ss')
    await loadStatus() // 保存后同步刷新生效值面板
  }
}
</script>

<style scoped lang="less">
.udesk-access-page {
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
.cs-spin { animation: udesk-spin 1s linear infinite; }
@keyframes udesk-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

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

.udesk-field {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  .cs-label { flex: none; width: 150px; text-align: right; color: var(--gray-700); }
  .udesk-hint { color: var(--gray-500); font-size: 12px; }
  :deep(.ant-input), :deep(.ant-input-number) { width: 320px; }
}
.udesk-token-alert { margin-top: 8px; }

.udesk-effective-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--gray-100);

  &:last-child { border-bottom: none; }

  .udesk-effective-label { flex: none; width: 150px; text-align: right; color: var(--gray-700); }
  .udesk-effective-value {
    flex: 1;
    min-width: 0;
    color: var(--gray-900);
    word-break: break-all;
  }
}
</style>
