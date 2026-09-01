<template>
  <div class="user-config-settings">
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">{{ $t('settings.userConfigTitle') }}</div>
      </div>
      <div class="header-actions">
        <a-button class="lucide-icon-btn" :loading="loading" @click="loadUserConfig">
          <template #icon><RefreshCw :size="16" :class="{ spin: loading }" /></template>
          {{ $t('common.refresh') }}
        </a-button>
      </div>
    </div>
    <a-spin :spinning="loading">
      <div class="config-panel">
        <div class="config-row">
          <div class="config-meta">
            <div class="config-title-line">
              <span class="config-title">{{ $t('settings.enableMemory') }}</span>
              <span class="reserved-badge">{{ $t('settings.reservedToggle') }}</span>
            </div>
            <p class="config-description">{{ $t('settings.memoryReservedDesc') }}</p>
          </div>
          <a-switch :checked="draftEnableMemory" @change="handleMemoryChange" />
        </div>
      </div>
    </a-spin>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { RefreshCw } from 'lucide-vue-next'
import { userConfigApi } from '@/apis/user_config_api'

const { t } = useI18n()

const loading = ref(false)
const saving = ref(false)
const draftEnableMemory = ref(false)
const savedEnableMemory = ref(false)

const applyResponse = (res) => {
  draftEnableMemory.value = res.enable_memory
  savedEnableMemory.value = res.enable_memory
}

const loadUserConfig = async () => {
  loading.value = true
  try {
    const res = await userConfigApi.get()
    applyResponse(res)
  } catch (error) {
    message.error(error.message || t('settings.loadUserConfigFailed'))
  } finally {
    loading.value = false
  }
}

const handleMemoryChange = (val) => {
  draftEnableMemory.value = Boolean(val)
  saveUserConfig()
}

const saveUserConfig = async () => {
  saving.value = true
  try {
    const res = await userConfigApi.update({ enable_memory: draftEnableMemory.value })
    applyResponse(res)
    message.success(t('settings.userConfigSaved'))
  } catch (error) {
    message.error(error.message || t('settings.saveUserConfigFailed'))
  } finally {
    saving.value = false
  }
}

onMounted(loadUserConfig)
</script>

<style lang="less" scoped>
.user-config-settings {
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

  .config-panel {
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    background: var(--gray-0);
    overflow: hidden;
  }

  .config-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    padding: 14px 16px;

    @media (max-width: 560px) {
      align-items: flex-start;
      flex-direction: column;
    }
  }

  .config-meta {
    min-width: 0;
  }

  .config-title-line {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .config-title {
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 500;
    line-height: 1.4;
  }

  .reserved-badge {
    display: inline-flex;
    align-items: center;
    height: 22px;
    padding: 0 8px;
    border-radius: 999px;
    border: 1px solid var(--color-warning-100);
    background: var(--color-warning-10);
    color: var(--color-warning-700);
    font-size: 12px;
    line-height: 1;
    white-space: nowrap;
  }

  .config-description {
    margin: 6px 0 0;
    color: var(--gray-600);
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
