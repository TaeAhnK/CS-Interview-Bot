import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import type { TtsBridge, TtsStatus } from './bridge'

type ReadingState = {
  itemId: string | number
  status: Exclude<TtsStatus['status'], 'idle'>
}

export default function useSpeechReading(
  bridge: Partial<TtsBridge> | undefined,
  scope: string | number | undefined,
  pendingStart: RefObject<Promise<void>>,
  deviceId: string | null,
  volume: number,
) {
  const [reading, setReading] = useState<ReadingState>()
  const active = useRef<{ stop: () => void } | null>(null)
  const output = useRef({ deviceId, volume })

  useLayoutEffect(() => {
    output.current = { deviceId, volume }
  }, [deviceId, volume])

  useLayoutEffect(() => {
    setReading(undefined)
    return () => active.current?.stop()
  }, [bridge, scope])

  const read = useCallback(async (itemId: ReadingState['itemId'], question?: string, answer?: string) => {
    active.current?.stop()
    const show = (status: ReadingState['status']) => setReading({ itemId, status })
    if (!bridge?.tts_status || !bridge.tts_stop || !(answer ? bridge.tts_play_study : question !== undefined ? bridge.tts_play : bridge.tts_test)) {
      show('failed')
      return
    }
    const api = bridge as TtsBridge
    const testOutput = output.current
    let requestId: number | undefined
    const stopRequest = (id: number) => { void api.tts_stop(id).catch(() => {}) }
    const session = {
      stop() {
        active.current = null
        if (requestId !== undefined) stopRequest(requestId)
      },
    }
    const isCurrent = () => active.current === session
    active.current = session
    show('synthesizing')

    try {
      // pywebview runs calls on separate threads. Wait for the previous start
      // to be acknowledged before sending a newer one.
      const start: Promise<TtsStatus | undefined> = pendingStart.current.then(async () => {
        if (!isCurrent()) return
        const { deviceId, volume } = question === undefined ? testOutput : output.current
        const result = question === undefined
          ? await api.tts_test(deviceId, volume)
          : answer
            ? await api.tts_play_study(question, answer, deviceId, volume)
            : await api.tts_play(question, deviceId, volume)
        if (!isCurrent()) {
          stopRequest(result.request_id)
          return
        }
        requestId = result.request_id
        return result
      })
      pendingStart.current = start.then(() => {}, () => {})
      let result = await start
      if (!result || !isCurrent()) return

      while (result.request_id === requestId && (result.status === 'synthesizing' || result.status === 'playing')) {
        show(result.status)
        await new Promise<void>((resolve) => window.setTimeout(resolve, 150))
        if (!isCurrent()) return
        result = await api.tts_status()
        if (!isCurrent()) return
      }

      if (result.request_id !== requestId || result.status === 'stopped') {
        show('stopped')
      } else {
        show(result.status === 'completed' ? 'completed' : 'failed')
      }
    } catch {
      if (isCurrent()) {
        session.stop()
        show('failed')
      }
    } finally {
      if (isCurrent()) active.current = null
    }
  }, [bridge, pendingStart])

  function stop() {
    active.current?.stop()
    setReading((current) => current && { ...current, status: 'stopped' })
  }

  return { reading, read, stop, isActive: () => active.current !== null }
}
