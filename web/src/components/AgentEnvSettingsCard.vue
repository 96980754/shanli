<template>
  <div class="agent-env-settings">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">{{ $t('agentCfg.sandboxEnvTitle') }}</div>
        <p class="section-description">
          {{ $t('agentCfg.sandboxEnvDescription') }}
        </p>
      </div>
      <div class="header-actions">
        <a-button class="lucide-icon-btn" :loading="loading" @click="loadAgentEnv">
          <template #icon><RefreshCw :size="16" :class="{ spin: loading }" /></template>
          {{ $t('common.refresh') }}
        </a-button>
        <a-button type="primary" :loading="saving" @click="saveAgentEnv">
          {{ saveButtonText }}
        </a-button>
      </div>
    </div>

    <div class="env-tip">{{ $t('agentCfg.sandboxEnvTip') }}</div>

    <a-spin :spinning="loading">
      <McpEnvEditor
        :key="editorRevision"
        :modelValue="draftEnv"
        :locked-keys="savedEnvKeys"
        conceal-locked-values
        @update:modelValue="updateDraftEnv"
      />
    </a-spin>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import { agentEnvApi } from '@/apis/agent_env_api'
import McpEnvEditor from '@/components/McpEnvEditor.vue'

const { t } = useI18n()

const ENV_KEY_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/
const MAX_ENV_COUNT = 200
const MAX_ENV_KEY_LENGTH = 128
const MAX_ENV_VALUE_LENGTH = 32768

const loading = ref(false)
const saving = ref(false)
const draftEnv = ref({})
const lastSavedEnv = ref({})
const editorRevision = ref(0)

const normalizeEnv = (env) => {
  if (!env || typeof env !== 'object' || Array.isArray(env)) {
    return {}
  }
  return Object.fromEntries(
    Object.entries(env)
      .map(([key, value]) => [key.trim(), value == null ? '' : String(value)])
      .filter(([key]) => key)
  )
}

const isSameEnv = (left, right) => {
  const leftEntries = Object.entries(left)
  const rightEntries = Object.entries(right)
  if (leftEntries.length !== rightEntries.length) return false
  return leftEntries.every(([key, value]) => right[key] === value)
}

const savedEnvKeys = computed(() => Object.keys(lastSavedEnv.value || {}))
const hasUnsavedChanges = computed(
  () => !isSameEnv(normalizeEnv(draftEnv.value), lastSavedEnv.value)
)
const saveButtonText = computed(() =>
  hasUnsavedChanges.value ? t('agentCfg.saveWithChanges') : t('common.save')
)

const updateDraftEnv = (value) => {
  const nextEnv = normalizeEnv(value)
  if (!isSameEnv(draftEnv.value, nextEnv)) {
    draftEnv.value = nextEnv
  }
}

const validateEnv = (env) => {
  const entries = Object.entries(env)
  if (entries.length > MAX_ENV_COUNT) {
    message.error(t('agentCfg.envMaxCount', { count: MAX_ENV_COUNT }))
    return false
  }

  for (const [key, value] of entries) {
    if (key.length > MAX_ENV_KEY_LENGTH) {
      message.error(t('agentCfg.envKeyMaxLength', { length: MAX_ENV_KEY_LENGTH }))
      return false
    }
    if (!ENV_KEY_PATTERN.test(key)) {
      message.error(t('agentCfg.envKeyInvalid', { key }))
      return false
    }
    if (value.length > MAX_ENV_VALUE_LENGTH) {
      message.error(t('agentCfg.envValueTooLong', { key }))
      return false
    }
  }
  return true
}

const loadAgentEnv = async () => {
  loading.value = true
  try {
    const res = await agentEnvApi.get()
    const env = normalizeEnv(res.env)
    draftEnv.value = env
    lastSavedEnv.value = env
    editorRevision.value += 1
  } catch (error) {
    message.error(error.message || t('agentCfg.loadEnvFailed'))
  } finally {
    loading.value = false
  }
}

const saveAgentEnv = async () => {
  const env = normalizeEnv(draftEnv.value)
  if (!validateEnv(env)) return
  if (isSameEnv(env, lastSavedEnv.value)) {
    message.info(t('agentCfg.envUnchanged'))
    return
  }

  saving.value = true
  try {
    await agentEnvApi.update(env)
    draftEnv.value = env
    lastSavedEnv.value = env
    editorRevision.value += 1
    message.success(t('agentCfg.envSaved'))
  } catch (error) {
    message.error(error.message || t('agentCfg.saveEnvFailed'))
  } finally {
    saving.value = false
  }
}

onMounted(loadAgentEnv)
</script>

<style lang="less" scoped>
.agent-env-settings {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 12px;

    @media (max-width: 760px) {
      align-items: stretch;
      flex-direction: column;
    }
  }

  .header-content {
    flex: 1;
    min-width: 0;
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
  }

  .env-tip {
    margin-bottom: 14px;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--main-10);
    border: 1px solid var(--main-300);
    color: var(--main-700);
    font-size: 13px;
    line-height: 1.5;
  }
}

:deep(.spin) {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}
</style>
