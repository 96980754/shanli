<template>
  <div class="voice-input" @click.stop>
    <a-tooltip :title="tooltip">
      <a-button
        v-if="state === 'idle' || state === 'error'"
        type="text"
        class="voice-button"
        :disabled="disabled"
        :aria-label="t('voice.start')"
        @mousedown.prevent
        @click="startRecording"
      >
        <template #icon><Mic :size="16" /></template>
      </a-button>
    </a-tooltip>

    <div v-if="state === 'recording'" class="recording-controls">
      <span class="recording-label"><span class="recording-dot"></span>{{ $t('voice.recording') }}</span>
      <a-button
        type="text"
        class="voice-button stop-button"
        :aria-label="t('voice.stop')"
        @mousedown.prevent
        @click="stopRecording"
      >
        <template #icon><Square :size="14" fill="currentColor" /></template>
      </a-button>
      <a-button
        type="text"
        class="voice-button"
        :aria-label="t('voice.cancel')"
        @mousedown.prevent
        @click="cancelRecording"
      >
        <template #icon><X :size="16" /></template>
      </a-button>
    </div>

    <div v-if="state === 'transcribing'" class="transcribing-label" aria-live="polite">
      <LoaderCircle :size="15" class="spin" />
      <span>{{ $t('voice.transcribing') }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { useI18n } from 'vue-i18n'
import { LoaderCircle, Mic, Square, X } from 'lucide-vue-next'

import { transcriptionApi } from '@/apis/agent_api'
import { createVoiceRecorder } from '@/utils/voiceRecorder'

const props = defineProps({
  disabled: { type: Boolean, default: false }
})
const emit = defineEmits(['transcript'])

const state = ref('idle')
const lastError = ref('')
const { t } = useI18n()

const formatError = (error) => {
  if (error?.code === 'unsupported') return t('voice.errUnsupported')
  if (error?.code === 'empty') return t('voice.errEmpty')
  if (error?.code === 'empty-transcript') return t('voice.errEmptyTranscript')
  if (error?.cause?.name === 'NotAllowedError') return t('voice.errPermissionDenied')
  if (error?.cause?.name === 'NotFoundError') return t('voice.errNoMic')
  if (error?.cause?.name === 'NotReadableError') return t('voice.errMicUnavailable')
  if (error?.code === 'permission-failed') return t('voice.errPermissionDenied')
  if (error?.code === 'transcription-failed') {
    return error.cause?.message || t('voice.errTranscription')
  }
  return t('voice.errRecording')
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

const tooltip = computed(() => lastError.value || t('voice.input'))

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
