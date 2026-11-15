import { useEffect, useRef, useState } from 'react'
import type { InterviewBridge, MicrophoneError, MicrophoneStatus, SettingsResult } from './bridge'

type Props = {
  bridge: InterviewBridge | undefined
  savedDeviceId: string | null
  savingDisabled: boolean
  applySettings: (call: () => Promise<SettingsResult>) => Promise<boolean>
}

type TestRequest = { cancel: () => void }

function testError(error: MicrophoneError | null): string {
  switch (error) {
    case 'permission_denied': return '마이크 접근 권한이 없습니다. Windows의 마이크 권한을 확인해 주세요.'
    case 'device_unavailable': return '선택한 마이크를 사용할 수 없습니다. Windows 기본 장치 또는 다른 마이크를 선택해 주세요.'
    case 'busy': return '마이크를 사용 중입니다. 잠시 후 다시 시도해 주세요.'
    default: return '마이크 테스트에 실패했습니다. Windows 기본 장치 또는 다른 마이크를 선택해 다시 시도해 주세요.'
  }
}

export default function MicrophoneSettings({ bridge, savedDeviceId, savingDisabled, applySettings }: Props) {
  const [devices, setDevices] = useState<{ id: string; name: string }[]>()
  const [listFailed, setListFailed] = useState(false)
  const [listAttempt, setListAttempt] = useState(0)
  const [draft, setDraft] = useState<{ deviceId: string | null }>()
  const [testing, setTesting] = useState(false)
  const [level, setLevel] = useState(0)
  const [notice, setNotice] = useState('')
  const [failure, setFailure] = useState('')
  const current = useRef<TestRequest | null>(null)
  const deviceId = draft ? draft.deviceId : savedDeviceId
  const missing = deviceId !== null && devices !== undefined && !devices.some(({ id }) => id === deviceId)

  useEffect(() => {
    let active = true
    setListFailed(false)
    async function load() {
      try {
        const result = await bridge?.mic_devices?.()
        if (active && result?.ok) setDevices(result.devices)
        else if (active) setListFailed(true)
      } catch {
        if (active) setListFailed(true)
      }
    }
    void load()
    return () => { active = false }
  }, [bridge, listAttempt])

  useEffect(() => {
    setTesting(false)
    setLevel(0)
    setFailure('')
    setNotice('')
    return () => {
      current.current?.cancel()
      current.current = null
    }
  }, [bridge])

  function stopTest(message = '') {
    current.current?.cancel()
    current.current = null
    setTesting(false)
    setLevel(0)
    setFailure('')
    setNotice(message)
  }

  async function startTest() {
    if (current.current || !bridge?.mic_start || !bridge.mic_status || !bridge.mic_cancel) return
    const { mic_start: start, mic_status: status, mic_cancel: cancel } = bridge
    let id: number | undefined
    let cancelled = false
    let poll: number | undefined
    async function cancelOwned(requestId: number) {
      try { await cancel(requestId) } catch { /* The backend also limits the request and releases it at window shutdown. */ }
    }
    const request: TestRequest = {
      cancel() {
        cancelled = true
        window.clearTimeout(poll)
        if (id !== undefined) void cancelOwned(id)
      },
    }
    current.current = request
    setTesting(true)
    setFailure('')
    setNotice('마이크를 여는 중입니다.')

    function finish(error: string, message = '') {
      if (current.current !== request) return
      current.current = null
      setLevel(0)
      setTesting(false)
      setFailure(error)
      setNotice(message)
    }

    function update(result: MicrophoneStatus) {
      if (current.current !== request) return
      if (!result.ok || result.status === 'failed') {
        if (!result.ok) request.cancel()
        finish(testError(result.error))
      } else if (result.status === 'cancelled' || result.status === 'completed') {
        finish('', result.reason === 'limit' ? '시간 제한에 도달해 마이크 테스트를 종료했습니다.' : '마이크 테스트가 종료되었습니다.')
      } else {
        setLevel(result.level)
        if (result.status === 'recording') setNotice('말하면서 입력 레벨을 확인해 주세요.')
        poll = window.setTimeout(async () => {
          try {
            if (id !== undefined) update(await status(id))
          } catch {
            if (current.current !== request) return
            request.cancel()
            finish(testError(null))
          }
        }, 100)
      }
    }

    try {
      const result = await start(deviceId, true)
      if (result.ok) {
        id = result.request_id
        if (cancelled) {
          void cancelOwned(id)
          return
        }
      }
      update(result)
    } catch {
      finish(testError(null))
    }
  }

  return (
    <fieldset className="space-y-2">
      <legend className="mb-2 text-sm font-medium text-slate-700">음성 입력</legend>
      <div className="device-picker">
      <label className="block min-w-0 text-sm">
        마이크
        <select className="settings-input" value={deviceId ?? ''} onChange={(event) => {
          stopTest()
          setDraft({ deviceId: event.target.value || null })
        }}>
          <option value="">Windows 기본 장치</option>
          {deviceId !== null && !devices?.some(({ id }) => id === deviceId) &&
            <option value={deviceId} disabled>{deviceId}{missing ? ' (사용할 수 없음)' : ''}</option>}
          {devices?.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
        </select>
      </label>
      <button type="button" className="interview-button" onClick={() => setListAttempt((attempt) => attempt + 1)}>
        마이크 목록 새로고침
      </button>
      </div>
      {listFailed ? <p role="alert" className="text-sm text-rose-800">마이크 목록을 불러오지 못했습니다. Windows 기본 장치를 선택할 수 있습니다.</p>
        : missing ? <p role="alert" className="text-sm text-rose-800">선택한 마이크를 사용할 수 없습니다. Windows 기본 장치 또는 다른 마이크를 선택해 주세요.</p>
        : null}
      <label className="block text-sm">
        마이크 입력 레벨 {Math.round(level * 100)}%
        <meter className="block w-full" aria-label="마이크 입력 레벨" min="0" max="1" value={level} />
      </label>
      <div className="flex flex-wrap gap-2">
        {testing ? <button type="button" className="interview-button" onClick={() => stopTest('마이크 테스트를 중지했습니다.')}>마이크 테스트 중지</button>
          : <button type="button" className="interview-button"
              disabled={missing || !bridge?.mic_start || !bridge.mic_status || !bridge.mic_cancel}
              onClick={() => void startTest()}>마이크 테스트</button>}
        <button type="button" className="interview-button ml-auto"
          disabled={!bridge?.save_mic_input || !draft || savingDisabled || missing || (deviceId !== null && !devices)}
          onClick={async () => {
            if (!bridge?.save_mic_input) return
            if (await applySettings(() => bridge.save_mic_input!(deviceId))) {
              setDraft((latest) => latest === draft ? undefined : latest)
            }
          }}>마이크 저장</button>
      </div>
      {failure && <p role="alert" className="text-sm text-rose-800">{failure}</p>}
      {notice && <p role="status" className="sr-only">{notice}</p>}
    </fieldset>
  )
}
