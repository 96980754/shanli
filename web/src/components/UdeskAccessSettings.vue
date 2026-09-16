<!-- Udesk 对接设置：非密参数（开关/子域名/账号/接口地址/签名算法/拉取参数）在设置页
     自助配置、保存即热同步生效；open_api_token 是永久凭证，只允许部署时写入 .env，
     本页不做 token 输入（避免凭证进配置文件/数据库，A9）。 -->

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

// token 永不出现在提交载荷（A9）：只提交本页可见字段。
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
  const { ok } = await configStore.setConfigValues(payload)
  saveState.value = ok ? 'saved' : 'error'
  if (ok) {
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
