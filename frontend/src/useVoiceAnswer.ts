import { useLayoutEffect, useRef, useState } from 'react'
import type { InterviewBridge, MicrophoneBridge, TranscriptionBridge } from './bridge'

type VoiceState = { phase: 'recording'; elapsedMs: number } | { phase: 'opening' | 'stopping' | 'transcribing' }
type Session = { cancel: () => void; stop: () => void }

function errorMessage(code: string): string {
  switch (code) {
    case 'permission_denied': return 'Windows에서 마이크 접근 권한을 허용해 주세요.'
    case 'device_unavailable': return '마이크를 사용할 수 없습니다. 설정에서 입력 장치를 확인해 주세요.'
    case 'busy': return '음성 장치를 정리 중이거나 사용 중입니다. 잠시 후 다시 시도해 주세요.'
    case 'engine_unavailable': return '로컬 음성 인식 프로그램을 실행할 수 없습니다.'
    case 'model_missing': return '로컬 음성 인식 모델을 찾을 수 없습니다.'
    case 'timeout': return '음성 변환 시간이 초과되었습니다. 다시 녹음할 수 있습니다.'
    case 'empty_recording': return '녹음된 입력이 없습니다. 다시 녹음할 수 있습니다.'
    default: return '음성 답변을 처리하지 못했습니다. 다시 녹음할 수 있습니다.'
  }
}

const poll = () => new Promise<void>((resolve) => window.setTimeout(resolve, 100))

export default function useVoiceAnswer(
  bridge: InterviewBridge | undefined,
  questionVersion: number | undefined,
  append: (text: string) => void,
) {
  const [state, setState] = useState<VoiceState>()
  const [notice, setNotice] = useState<{ text: string; error?: boolean }>()
  const active = useRef<Session | null>(null)
  const available = !!(bridge?.mic_start && bridge.mic_status && bridge.mic_stop && bridge.mic_cancel &&
    bridge.stt_start && bridge.stt_status && bridge.stt_take_result && bridge.stt_cancel)

  useLayoutEffect(() => {
    setState(undefined)
    setNotice(undefined)
    return () => {
      active.current?.cancel()
      active.current = null
    }
  }, [bridge, questionVersion])

  async function start(deviceId: string | null) {
    if (!available || questionVersion === undefined || active.current) return
    const api = bridge as MicrophoneBridge & TranscriptionBridge
    let recordingId: number | undefined
    let transcriptionId: number | undefined
    let stopSent = false
    let stopError = false
    const cancelRecording = (id: number) => { void api.mic_cancel(id).catch(() => {}) }
    const cancelTranscription = (id: number) => { void api.stt_cancel(id).catch(() => {}) }
    const session: Session = {
      cancel() {
        if (recordingId !== undefined) cancelRecording(recordingId)
        if (transcriptionId !== undefined) cancelTranscription(transcriptionId)
      },
      stop() {
        if (!isCurrent() || recordingId === undefined || stopSent) return
        stopSent = true
        setState({ phase: 'stopping' })
        // The polling loop alone advances to transcription, even if stop and the limit race.
        void api.mic_stop(recordingId).then((result) => {
          if (!result.ok) stopError = true
        }, () => { stopError = true })
      },
    }
    const isCurrent = () => active.current === session
    active.current = session
    setState({ phase: 'opening' })
    setNotice(undefined)

    try {
      let mic = await api.mic_start(deviceId, false)
      if (mic.ok) recordingId = mic.request_id
      if (!isCurrent()) {
        if (recordingId !== undefined) cancelRecording(recordingId)
        return
      }
      if (!mic.ok) throw Error(mic.error)
      const ownedRecording = mic.request_id
      while (mic.ok && (mic.status === 'starting' || mic.status === 'recording' || mic.status === 'stopping')) {
        if (stopError) throw Error('capture_failed')
        if (stopSent || mic.status === 'stopping') setState({ phase: 'stopping' })
        else if (mic.status === 'recording') setState({ phase: 'recording', elapsedMs: mic.elapsed_ms })
        await poll()
        if (!isCurrent()) return
        mic = await api.mic_status(ownedRecording)
        if (!isCurrent()) return
      }
      if (!mic.ok || mic.status !== 'completed' || !mic.has_recording) {
        throw Error(mic.error ?? 'capture_failed')
      }

      setState({ phase: 'transcribing' })
      let stt = await api.stt_start(ownedRecording)
      if (stt.ok) transcriptionId = stt.request_id
      if (!isCurrent()) {
        if (transcriptionId !== undefined) cancelTranscription(transcriptionId)
        return
      }
      if (!stt.ok) throw Error(stt.error)
      recordingId = undefined
      const ownedTranscription = stt.request_id
      while (stt.ok && stt.status === 'transcribing') {
        await poll()
        if (!isCurrent()) return
        stt = await api.stt_status(ownedTranscription)
        if (!isCurrent()) return
      }
      if (stt.ok && stt.status === 'empty') {
        setNotice({ text: '인식된 내용이 없습니다. 다시 녹음할 수 있습니다.' })
        return
      }
      if (!stt.ok || stt.request_id !== ownedTranscription || stt.recording_id !== ownedRecording ||
          stt.status !== 'completed' || !stt.has_result) {
        throw Error(stt.error ?? 'transcription_failed')
      }
      const result = await api.stt_take_result(ownedTranscription)
      if (!isCurrent()) return
      if (!result.ok || result.request_id !== ownedTranscription || result.recording_id !== ownedRecording) {
        throw Error('result_unavailable')
      }
      append(result.text)
      setNotice({ text: '음성을 답변에 추가했습니다. 내용을 확인한 뒤 제출하세요.' })
    } catch (error) {
      if (!isCurrent()) return
      session.cancel()
      setNotice({ text: errorMessage(error instanceof Error ? error.message : ''), error: true })
    } finally {
      if (isCurrent()) {
        active.current = null
        setState(undefined)
      }
    }
  }

  function cancel(showNotice = false) {
    if (!active.current) return
    active.current.cancel()
    active.current = null
    setState(undefined)
    setNotice(showNotice ? { text: '음성 답변을 취소했습니다. 다시 녹음할 수 있습니다.' } : undefined)
  }

  return { available, state, notice, start, stop: () => active.current?.stop(), cancel, isActive: () => !!active.current }
}
