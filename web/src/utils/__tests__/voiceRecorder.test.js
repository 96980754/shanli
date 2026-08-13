import assert from 'node:assert/strict'

import { createVoiceRecorder, selectRecordingMimeType } from '../voiceRecorder.js'

const nextTurn = () => new Promise((resolve) => setTimeout(resolve, 0))

class FakeTrack {
  stopCount = 0

  stop() {
    this.stopCount += 1
  }
}

class FakeMediaRecorder {
  static emitAudio = true

  static isTypeSupported(type) {
    return type === 'audio/webm;codecs=opus'
  }

  constructor(stream, options) {
    this.stream = stream
    this.mimeType = options.mimeType
    this.state = 'inactive'
  }

  start() {
    this.state = 'recording'
  }

  stop() {
    this.state = 'inactive'
    if (FakeMediaRecorder.emitAudio) {
      this.ondataavailable?.({ data: new Blob(['webm-audio'], { type: this.mimeType }) })
    }
    queueMicrotask(() => this.onstop?.())
  }
}

const createHarness = ({ getUserMedia, transcribe } = {}) => {
  const track = new FakeTrack()
  const stream = { getTracks: () => [track] }
  let getUserMediaCount = 0
  const states = []
  const transcripts = []
  const errors = []
  const recorder = createVoiceRecorder({
    mediaDevices: {
      getUserMedia: async () => {
        getUserMediaCount += 1
        return getUserMedia ? getUserMedia() : stream
      }
    },
    MediaRecorderClass: FakeMediaRecorder,
    transcribe: transcribe || (async () => ({ text: '语音文本' })),
    onStateChange: (state) => states.push(state),
    onTranscript: (text) => transcripts.push(text),
    onError: (error) => errors.push(error)
  })
  return {
    recorder,
    track,
    states,
    transcripts,
    errors,
    getUserMediaCount: () => getUserMediaCount
  }
}

const run = async () => {
  assert.equal(selectRecordingMimeType(FakeMediaRecorder), 'audio/webm;codecs=opus')
  assert.equal(selectRecordingMimeType(null), '')

  const success = createHarness()
  assert.equal(await success.recorder.start(), true)
  assert.equal(success.recorder.state, 'recording')
  assert.equal(await success.recorder.start(), false)
  assert.equal(success.getUserMediaCount(), 1)
  assert.equal(success.recorder.stop(), true)
  assert.equal(success.recorder.state, 'transcribing')
  await nextTurn()
  assert.deepEqual(success.transcripts, ['语音文本'])
  assert.equal(success.recorder.state, 'idle')
  assert.equal(success.track.stopCount, 1)

  let resolvePendingPermission
  const pendingPermission = createHarness({
    getUserMedia: () =>
      new Promise((resolve) => {
        resolvePendingPermission = resolve
      })
  })
  const firstStart = pendingPermission.recorder.start()
  assert.equal(await pendingPermission.recorder.start(), false)
  assert.equal(pendingPermission.getUserMediaCount(), 1)
  resolvePendingPermission({ getTracks: () => [pendingPermission.track] })
  assert.equal(await firstStart, true)
  pendingPermission.recorder.cancel()

  let canceledTranscriptionCount = 0
  const canceled = createHarness({
    transcribe: async () => {
      canceledTranscriptionCount += 1
      return { text: '不应出现' }
    }
  })
  await canceled.recorder.start()
  assert.equal(canceled.recorder.cancel(), true)
  await nextTurn()
  assert.equal(canceledTranscriptionCount, 0)
  assert.deepEqual(canceled.transcripts, [])
  assert.equal(canceled.track.stopCount, 1)

  const permissionError = Object.assign(new Error('denied'), { name: 'NotAllowedError' })
  const permission = createHarness({
    getUserMedia: () => Promise.reject(permissionError)
  })
  assert.equal(await permission.recorder.start(), false)
  assert.equal(permission.recorder.state, 'error')
  assert.equal(permission.errors[0].cause.name, 'NotAllowedError')

  const unsupportedErrors = []
  const unsupported = createVoiceRecorder({
    mediaDevices: null,
    MediaRecorderClass: null,
    transcribe: async () => ({ text: '' }),
    onError: (error) => unsupportedErrors.push(error)
  })
  assert.equal(await unsupported.start(), false)
  assert.equal(unsupported.state, 'error')
  assert.equal(unsupportedErrors[0].code, 'unsupported')

  const draft = { value: '补充说明：' }
  let sendCount = 0
  const preservesDraft = createVoiceRecorder({
    mediaDevices: { getUserMedia: async () => ({ getTracks: () => [new FakeTrack()] }) },
    MediaRecorderClass: FakeMediaRecorder,
    transcribe: async () => ({ text: '这是语音输入测试' }),
    onTranscript: (text) => {
      draft.value += text
    }
  })
  await preservesDraft.start()
  preservesDraft.stop()
  await nextTurn()
  assert.equal(draft.value, '补充说明：这是语音输入测试')
  assert.equal(sendCount, 0)

  const failed = createHarness({
    transcribe: async () => {
      throw new Error('network failed')
    }
  })
  await failed.recorder.start()
  failed.recorder.stop()
  await nextTurn()
  assert.equal(failed.recorder.state, 'error')
  assert.equal(failed.track.stopCount, 1)
  assert.equal(failed.errors[0].code, 'transcription-failed')

  const disposed = createHarness()
  await disposed.recorder.start()
  disposed.recorder.dispose()
  await nextTurn()
  assert.equal(disposed.track.stopCount, 1)
  assert.deepEqual(disposed.transcripts, [])

  FakeMediaRecorder.emitAudio = false
  const empty = createHarness()
  await empty.recorder.start()
  empty.recorder.stop()
  await nextTurn()
  assert.equal(empty.recorder.state, 'error')
  assert.equal(empty.errors[0].code, 'empty')
  FakeMediaRecorder.emitAudio = true

  console.log('voiceRecorder: all assertions passed')
}

await run()
