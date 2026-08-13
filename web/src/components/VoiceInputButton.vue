<template>
  <div class="voice-input" @click.stop>
    <a-tooltip :title="tooltip">
      <a-button
        v-if="state === 'idle' || state === 'error'"
        type="text"
        class="voice-button"
        :disabled="disabled"
        aria-label="开始语音输入"
        @mousedown.prevent
        @click="startRecording"
      >
        <template #icon><Mic :size="16" /></template>
      </a-button>
    </a-tooltip>

    <div v-if="state === 'recording'" class="recording-controls">
      <span class="recording-label"><span class="recording-dot"></span>正在录音</span>
      <a-button
        type="text"
        class="voice-button stop-button"
        aria-label="停止录音"
        @mousedown.prevent
        @click="stopRecording"
      >
        <template #icon><Square :size="14" fill="currentColor" /></template>
      </a-button>
      <a-button
        type="text"
        class="voice-button"
        aria-label="取消录音"
        @mousedown.prevent
        @click="cancelRecording"
      >
        <template #icon><X :size="16" /></template>
      </a-button>
    </div>

    <div v-if="state === 'transcribing'" class="transcribing-label" aria-live="polite">
      <LoaderCircle :size="15" class="spin" />
      <span>转写中</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { LoaderCircle, Mic, Square, X } from 'lucide-vue-next'

import { transcriptionApi } from '@/apis/agent_api'
import { createVoiceRecorder } from '@/utils/voiceRecorder'

const props = defineProps({
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['transcript'])

const state = ref('idle')
const lastError = ref('')

const formatError = (error) => {
  if (error?.code === 'unsupported') return '当前浏览器不支持 WebM 录音'
  if (error?.code === 'empty') return '没有录到有效音频，请重试'
  if (error?.code === 'empty-transcript') return '未识别到语音内容，请重试'
  if (error?.cause?.name === 'NotAllowedError') return '麦克风权限被拒绝，请在浏览器设置中允许访问'
  if (error?.cause?.name === 'NotFoundError') return '未检测到可用麦克风'
  if (error?.cause?.name === 'NotReadableError') return '麦克风暂时不可用，可能正被其他应用占用'
  if (error?.code === 'permission-failed') return '麦克风权限被拒绝，请在浏览器设置中允许访问'
  if (error?.code === 'transcription-failed') {
    return error.cause?.message || '语音转写失败，请稍后重试'
  }
  return '录音失败，请稍后重试'
}

const recorder = createVoiceRecorder({
  mediaDevices: typeof navigator !== 'undefined' ? navigator.mediaDevices : null,
  MediaRecorderClass: typeof MediaRecorder !== 'undefined' ? MediaRecorder : null,
  transcribe: (audioBlob) => transcriptionApi.transcribe(audioBlob),
  onStateChange: (nextState) => {
    state.value = nextState
    if (nextState !== 'error') lastError.value = ''
  },
  onTranscript: (text) => emit('transcript', text),
  onError: (error) => {
    lastError.value = formatError(error)
    message.error(lastError.value)
  }
})

const tooltip = computed(() => lastError.value || '语音输入')

const startRecording = () => {
  if (!props.disabled) void recorder.start()
}
const stopRecording = () => recorder.stop()
const cancelRecording = () => recorder.cancel()

watch(
  () => props.disabled,
  (disabled) => {
    if (disabled && state.value === 'recording') recorder.cancel()
  }
)

onBeforeUnmount(() => recorder.dispose())
</script>

<style lang="less" scoped>
.voice-input,
.recording-controls,
.transcribing-label,
.recording-label {
  display: inline-flex;
  align-items: center;
}

.recording-controls {
  gap: 2px;
}

.voice-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  color: var(--gray-600);
}

.recording-label,
.transcribing-label {
  gap: 6px;
  color: var(--gray-600);
  font-size: 12px;
  white-space: nowrap;
}

.recording-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--color-error-500);
  animation: pulse 1.2s ease-in-out infinite;
}

.stop-button {
  color: var(--color-error-500);
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
