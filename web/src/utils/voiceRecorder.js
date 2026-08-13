export class VoiceRecorderError extends Error {
  constructor(code, cause = null) {
    super(code)
    this.name = 'VoiceRecorderError'
    this.code = code
    this.cause = cause
  }
}

export const selectRecordingMimeType = (MediaRecorderClass) => {
  if (!MediaRecorderClass) return ''
  const candidates = ['audio/webm;codecs=opus', 'audio/webm']
  return candidates.find((type) => MediaRecorderClass.isTypeSupported?.(type)) || ''
}

export const createVoiceRecorder = ({
  mediaDevices,
  MediaRecorderClass,
  transcribe,
  onStateChange = () => {},
  onTranscript = () => {},
  onError = () => {}
}) => {
  let state = 'idle'
  let recorder = null
  let stream = null
  let chunks = []
  let canceled = false
  let disposed = false
  let starting = false
  let mimeType = ''

  const setState = (nextState) => {
    state = nextState
    onStateChange(nextState)
  }

  const releaseStream = () => {
    stream?.getTracks?.().forEach((track) => track.stop())
    stream = null
  }

  const fail = (error) => {
    releaseStream()
    chunks = []
    setState('error')
    onError(error)
  }

  const handleRecorderStop = async () => {
    if (canceled || disposed) {
      chunks = []
      recorder = null
      if (!disposed && state !== 'error') setState('idle')
      return
    }

    const audioBlob = new Blob(chunks, { type: mimeType })
    chunks = []
    recorder = null
    if (!audioBlob.size) {
      fail(new VoiceRecorderError('empty'))
      return
    }

    try {
      const result = await transcribe(audioBlob)
      if (disposed) return
      const text = String(result?.text || '').trim()
      if (!text) {
        fail(new VoiceRecorderError('empty-transcript'))
        return
      }
      onTranscript(text)
      setState('idle')
    } catch (error) {
      if (!disposed) fail(new VoiceRecorderError('transcription-failed', error))
    }
  }

  const start = async () => {
    if (starting || !['idle', 'error'].includes(state)) return false
    canceled = false
    mimeType = selectRecordingMimeType(MediaRecorderClass)
    if (!mediaDevices?.getUserMedia || !mimeType) {
      fail(new VoiceRecorderError('unsupported'))
      return false
    }

    try {
      starting = true
      stream = await mediaDevices.getUserMedia({ audio: true })
      starting = false
      if (disposed) {
        releaseStream()
        return false
      }

      chunks = []
      recorder = new MediaRecorderClass(stream, { mimeType })
      recorder.ondataavailable = (event) => {
        if (event.data?.size) chunks.push(event.data)
      }
      recorder.onstop = handleRecorderStop
      recorder.onerror = (event) => {
        canceled = true
        fail(new VoiceRecorderError('recording-failed', event.error))
      }
      recorder.start()
      setState('recording')
      return true
    } catch (error) {
      starting = false
      fail(new VoiceRecorderError('permission-failed', error))
      return false
    }
  }

  const stop = () => {
    if (state !== 'recording' || !recorder) return false
    setState('transcribing')
    try {
      recorder.stop()
      releaseStream()
      return true
    } catch (error) {
      fail(new VoiceRecorderError('recording-failed', error))
      return false
    }
  }

  const cancel = () => {
    if (state !== 'recording' || !recorder) return false
    canceled = true
    try {
      if (recorder.state !== 'inactive') recorder.stop()
    } finally {
      releaseStream()
      chunks = []
      setState('idle')
    }
    return true
  }

  const dispose = () => {
    disposed = true
    canceled = true
    starting = false
    try {
      if (recorder?.state !== 'inactive') recorder.stop()
    } finally {
      releaseStream()
      chunks = []
      recorder = null
    }
  }

  return {
    get state() {
      return state
    },
    start,
    stop,
    cancel,
    dispose
  }
}
