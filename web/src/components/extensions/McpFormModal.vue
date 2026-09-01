<template>
  <a-modal
    v-model:open="visible"
    :title="editMode ? $t('mcp.editTitle') : $t('mcp.addTitle')"
    @ok="handleFormSubmit"
    :confirmLoading="formLoading"
    @cancel="visible = false"
    :maskClosable="false"
    width="560px"
    class="server-modal"
  >
    <a-form layout="vertical" class="extension-form">
      <a-form-item :label="$t('mcp.form.slug')" required class="form-item">
        <a-input
          v-model:value="form.slug"
          :placeholder="$t('mcp.placeholder.slug')"
          :disabled="editMode"
        />
      </a-form-item>
      <a-form-item :label="$t('mcp.form.name')" required class="form-item">
        <a-input v-model:value="form.name" :placeholder="$t('mcp.placeholder.name')" />
      </a-form-item>
      <a-form-item :label="$t('mcp.form.description')" class="form-item">
        <a-input v-model:value="form.description" :placeholder="$t('mcp.placeholder.description')" />
      </a-form-item>
      <a-row :gutter="16">
        <a-col :span="12">
          <a-form-item :label="$t('mcp.form.transport')" required class="form-item">
            <a-select v-model:value="form.transport">
              <a-select-option value="streamable_http">streamable_http</a-select-option>
              <a-select-option value="sse">sse</a-select-option>
              <a-select-option value="stdio">stdio</a-select-option>
            </a-select>
          </a-form-item>
        </a-col>
        <a-col :span="12">
          <a-form-item :label="$t('mcp.form.icon')" class="form-item">
            <a-input v-model:value="form.icon" :placeholder="$t('mcp.placeholder.icon')" :maxlength="2" />
          </a-form-item>
        </a-col>
      </a-row>
      <template v-if="form.transport === 'streamable_http' || form.transport === 'sse'">
        <a-form-item :label="$t('mcp.form.url')" required class="form-item">
          <a-input v-model:value="form.url" placeholder="https://example.com/mcp" />
        </a-form-item>
        <a-form-item :label="$t('mcp.form.headers')" class="form-item">
          <a-textarea
            v-model:value="form.headersText"
            :placeholder="$t('mcp.placeholder.headers')"
            :rows="3"
          />
        </a-form-item>
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item :label="$t('mcp.form.httpTimeout')" class="form-item">
              <a-input-number
                v-model:value="form.timeout"
                :min="1"
                :max="300"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item :label="$t('mcp.form.sseReadTimeout')" class="form-item">
              <a-input-number
                v-model:value="form.sse_read_timeout"
                :min="1"
                :max="300"
                style="width: 100%"
              />
            </a-form-item>
          </a-col>
        </a-row>
      </template>
      <template v-if="isStdioTransport">
        <a-form-item :label="$t('mcp.form.command')" required class="form-item">
          <a-input v-model:value="form.command" :placeholder="$t('mcp.placeholder.command')" />
        </a-form-item>
        <a-form-item :label="$t('mcp.form.args')" class="form-item">
          <a-select
            v-model:value="form.args"
            mode="tags"
            :placeholder="$t('mcp.placeholder.args')"
            style="width: 100%"
          />
        </a-form-item>
        <a-form-item :label="$t('mcp.form.env')" class="form-item">
          <McpEnvEditor v-model="form.env" />
        </a-form-item>
      </template>
      <a-form-item :label="$t('mcp.form.tags')" class="form-item">
        <a-select
          v-model:value="form.tags"
          mode="tags"
          :placeholder="$t('mcp.placeholder.tags')"
          style="width: 100%"
        />
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { message } from 'ant-design-vue'
import { mcpApi } from '@/apis/mcp_api'
import McpEnvEditor from '@/components/McpEnvEditor.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  editMode: { type: Boolean, default: false },
  editData: { type: Object, default: null }
})

const emit = defineEmits(['update:open', 'submitted'])
const { t } = useI18n()

const visible = computed({
  get: () => props.open,
  set: (val) => emit('update:open', val)
})

const formLoading = ref(false)

const form = reactive({
  slug: '',
  name: '',
  description: '',
  transport: 'streamable_http',
  url: '',
  command: '',
  args: [],
  env: null,
  headersText: '',
  timeout: null,
  sse_read_timeout: null,
  tags: [],
  icon: ''
})

const isStdioTransport = computed(
  () =>
    String(form.transport || '')
      .trim()
      .toLowerCase() === 'stdio'
)

watch(
  () => props.open,
  (val) => {
    if (val && props.editData) {
      Object.assign(form, {
        slug: props.editData.slug || '',
        name: props.editData.name || '',
        description: props.editData.description || '',
        transport: props.editData.transport || 'streamable_http',
        url: props.editData.url || '',
        command: props.editData.command || '',
        args: props.editData.args || [],
        env: props.editData.env || null,
        headersText: props.editData.headers ? JSON.stringify(props.editData.headers, null, 2) : '',
        timeout: props.editData.timeout,
        sse_read_timeout: props.editData.sse_read_timeout,
        tags: props.editData.tags || [],
        icon: props.editData.icon || ''
      })
    } else if (val && !props.editData) {
      Object.assign(form, {
        slug: '',
        name: '',
        description: '',
        transport: 'streamable_http',
        url: '',
        command: '',
        args: [],
        env: null,
        headersText: '',
        timeout: null,
        sse_read_timeout: null,
        tags: [],
        icon: ''
      })
    }
  },
  { immediate: true }
)

const handleFormSubmit = async () => {
  try {
    formLoading.value = true
    let headers = null
    if (form.headersText.trim()) {
      try {
        headers = JSON.parse(form.headersText)
      } catch {
        message.error(t('mcp.headersJsonError'))
        return
      }
    }
    const data = {
      slug: form.slug,
      name: form.name,
      description: form.description || null,
      transport: form.transport,
      url: form.url || null,
      command: form.command || null,
      args: form.args.length > 0 ? form.args : null,
      env: form.env,
      headers,
      timeout: form.timeout || null,
      sse_read_timeout: form.sse_read_timeout || null,
      tags: form.tags.length > 0 ? form.tags : null,
      icon: form.icon || null
    }
    if (!data.slug?.trim()) {
      message.error(t('mcp.validateSlugRequired'))
      return
    }
    if (!data.name?.trim()) {
      message.error(t('mcp.validateNameRequired'))
      return
    }
    if (!data.transport) {
      message.error(t('mcp.validateTransportRequired'))
      return
    }
    if (['sse', 'streamable_http'].includes(data.transport)) {
      if (!data.url?.trim()) {
        message.error(t('mcp.validateUrlRequired'))
        return
      }
    }
    if (data.transport === 'stdio') {
      if (!data.command?.trim()) {
        message.error(t('mcp.validateCommandRequired'))
        return
      }
    }

    if (props.editMode) {
      const { slug, ...updateData } = data
      const result = await mcpApi.updateMcpServer(props.editData?.slug || slug, updateData)
      if (result.success) {
        message.success(t('mcp.updateSuccess'))
      } else {
        message.error(result.message || t('mcp.updateFail'))
        return
      }
    } else {
      const result = await mcpApi.createMcpServer(data)
      if (result.success) {
        message.success(t('mcp.createSuccess'))
      } else {
        message.error(result.message || t('mcp.createFail'))
        return
      }
    }
    visible.value = false
    emit('submitted')
  } catch (err) {
    message.error(err.message || t('mcp.operationFail'))
  } finally {
    formLoading.value = false
  }
}
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';
</style>
