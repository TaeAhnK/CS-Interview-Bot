import { memo, useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { ConceptRecord, InterviewBridge, InterviewState, KnowledgeLibraryData, ProviderId, SettingsData, SettingsErrorCode, SettingsResult } from './bridge'

declare global {
  interface Window {
    pywebview?: { api?: InterviewBridge }
  }
}

type IconName =
  | "chevron"
  | "gear"
  | "menu"
  | "message"
  | "send"
  | "x";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    chevron: <path d="m9 18 6-6-6-6" />,
    gear: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3A1.7 1.7 0 0 0 10 3v-.2h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1Z" />
      </>
    ),
    menu: <path d="M4 7h16M4 12h16M4 17h16" />,
    message: <path d="M20 15a3 3 0 0 1-3 3H8l-4 3V7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3Z" />,
    send: <path d="m21 3-7.5 18-3-7.5L3 10.5 21 3Zm-10.5 10.5L15 9" />,
    x: <path d="M6 6l12 12M18 6 6 18" />,
  };
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}

const evaluationLabels = [
  ['correct_points', '정확한 내용'],
  ['missing_points', '부족한 내용'],
  ['incorrect_points', '잘못된 내용'],
] as const

const InterviewRecord = memo(function InterviewRecord({ record }: { record: ConceptRecord }) {
  return (
    <section className="border-t border-slate-200 pt-4">
      <h3 className="font-semibold">{record.concept_name}</h3>
      {record.turns.map(({ question, answer, evaluation }, index) => (
        <div className="mt-4 space-y-3 text-sm leading-6" key={index}>
          <p className="font-medium text-slate-700">{question}</p>
          <div className="ml-auto max-w-[86%]">
            <p className="mb-1 text-right text-xs font-semibold text-slate-500">나의 답변</p>
            <p className="whitespace-pre-wrap break-words rounded-xl rounded-tr-sm bg-slate-100 px-5 py-3 text-slate-800">{answer}</p>
          </div>
          {evaluation && (
            <dl className="max-w-[90%] space-y-3 rounded-xl rounded-tl-sm border border-slate-200 bg-slate-50 px-5 py-4 text-slate-700">
              {evaluationLabels.map(([key, label]) => {
                const points = evaluation[key]
                return (
                  <div key={key}>
                    <dt className="font-medium">{label}</dt>
                    <dd>{points.length ? <ul className="ml-5 list-disc">
                      {points.map((point, index) => <li key={index}>{point}</li>)}
                    </ul> : '해당 없음'}</dd>
                  </div>
                )
              })}
            </dl>
          )}
        </div>
      ))}
      {record.unanswered_questions.map((question) => (
        <p className="mt-3 text-slate-600" key={question}>{question} <span className="font-medium">· 미응답</span></p>
      ))}
    </section>
  )
})

type Operation = 'evaluate' | 'redraw' | 'next' | 'end' | 'scope'
const scopeBatchDelayMs = 75

function sameScope(left: ReadonlySet<string>, right: ReadonlySet<string>) {
  if (left.size !== right.size) return false
  for (const id of left) {
    if (!right.has(id)) return false
  }
  return true
}

type AnswerFormProps = {
  state: InterviewState
  operation?: Operation
  hasFollowUp: boolean
  onSubmit: (draft: string) => void
  onRedraw: () => void
  onNext: () => void
  onEnd: () => void
}

function AnswerForm({ state, operation, hasFollowUp, onSubmit, onRedraw, onNext, onEnd }: AnswerFormProps) {
  const [draft, setDraft] = useState('')
  const evaluating = operation === 'evaluate' || state.busy
  const failed = state.status === 'error'

  return (
    <form className="answer-form" onSubmit={(event) => {
      event.preventDefault()
      if (failed || draft.trim()) onSubmit(draft)
    }}>
      <label className="mb-2 block text-xs font-medium text-slate-600" htmlFor="answer">나의 답변</label>
      <div className="answer-box">
        <textarea
          id="answer"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          readOnly={!!operation || !!evaluating || failed}
          rows={4}
          placeholder="답변을 입력하세요"
          className="block min-h-24 w-full resize-y bg-transparent px-1 py-1 text-sm leading-6 text-slate-700 outline-none placeholder:text-slate-400"
        />
        {evaluating && <p role="status" className="text-blue-800">답변을 평가하고 있습니다.</p>}
        {failed && !evaluating && <p role="alert" className="text-rose-800">{state.error} 같은 답변을 재시도하거나 면접을 종료할 수 있습니다.</p>}
        <div className="answer-actions">
          {state.status === 'ready' && (
            <button type="button" className="interview-button" disabled={!!operation} onClick={onRedraw}>
              새 질문 선택
            </button>
          )}
          {hasFollowUp && (
            <button type="button" className="interview-button" disabled={!!operation || evaluating || failed} onClick={onNext}>
              다음 개념
            </button>
          )}
          <button type="button" className="interview-button" disabled={!!operation && operation !== 'evaluate'} onClick={onEnd}>
            면접 종료
          </button>
          <button className="interview-button primary submit-answer" disabled={!!operation || !!evaluating || (!failed && !draft.trim())}>
            {failed ? '재시도' : '답변 제출'} <Icon name="send" />
          </button>
        </div>
      </div>
    </form>
  )
}

function indexLibrary(data: KnowledgeLibraryData) {
  const topics = data.topics.map(({ topic }) => {
    const units = topic.units.map((unit) => {
      const key = `${topic.id}/${unit.id}`
      const concepts = unit.concepts.map((concept) => ({ topic, unit, concept, id: `${key}/${concept.id}` }))
      return { unit, key, concepts, ids: concepts.map(({ id }) => id) }
    })
    return { topic, units, ids: units.flatMap(({ ids }) => ids) }
  })
  return { topics, errors: data.errors, concepts: topics.flatMap(({ units }) => units.flatMap(({ concepts }) => concepts)) }
}

export default function App() {
  const [bridge, setBridge] = useState(window.pywebview?.api)
  const [state, setState] = useState<InterviewState>()
  const [answerVersion, setAnswerVersion] = useState(0)
  const [operation, setOperation] = useState<Operation>()
  const [bridgeError, setBridgeError] = useState('')
  const settingsDialog = useRef<HTMLDialogElement>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settings, setSettings] = useState<SettingsData>()
  const [settingsReadError, setSettingsReadError] = useState('')
  const [settingsReadAttempt, setSettingsReadAttempt] = useState(0)
  const [settingsDraft, setSettingsDraft] = useState<Pick<SettingsData, 'provider' | 'models'>>()
  const [settingsMessage, setSettingsMessage] = useState('')
  const [keyDrafts, setKeyDrafts] = useState<Record<ProviderId, string>>({ gemini: '', openai: '' })
  const [settingsBusy, setSettingsBusy] = useState(false)
  const [library, setLibrary] = useState<ReturnType<typeof indexLibrary>>()
  const [libraryError, setLibraryError] = useState(false)
  const [libraryAttempt, setLibraryAttempt] = useState(0)
  const [initialScopeError, setInitialScopeError] = useState(false)
  const [initialScopeAttempt, setInitialScopeAttempt] = useState(0)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [openUnits, setOpenUnits] = useState<string[]>([])
  const [learningConcept, setLearningConcept] = useState<string>()
  const [activeTab, setActiveTab] = useState<'learn' | 'interview'>('learn')
  const pending = useRef<Operation | undefined>(undefined)
  const requestId = useRef(0)
  const requestedScope = useRef(selected)
  const appliedScope = useRef(selected)
  // Each successful settings write creates a token for requests that overlap the write.
  const savedEvaluationMode = useRef<{ mode: InterviewState['evaluation_mode'] } | undefined>(undefined)

  const acceptInterviewState = useCallback((next: InterviewState, modeAtRequestStart: typeof savedEvaluationMode.current) => {
    const latest = savedEvaluationMode.current
    if (next.status === 'ready' && latest && latest !== modeAtRequestStart) {
      setState({ ...next, evaluation_mode: latest.mode })
    } else {
      setState(next)
    }
  }, [])

  useEffect(() => {
    const connect = () => {
      if (window.pywebview?.api) setBridge(window.pywebview.api)
    }
    window.addEventListener('pywebviewready', connect)
    const poll = bridge ? undefined : window.setInterval(connect, 100)
    connect()
    return () => {
      window.removeEventListener('pywebviewready', connect)
      window.clearInterval(poll)
    }
  }, [bridge])

  useEffect(() => {
    if (!bridge) return
    let active = true
    async function readSettings(api: InterviewBridge) {
      setSettingsReadError('')
      try {
        const result = await api.settings_state()
        if (!active) return
        if (result.ok) setSettings(result.settings)
        else setSettingsReadError(settingsErrorText(result.error))
      } catch {
        if (active) setSettingsReadError('설정을 불러오지 못했습니다.')
      }
    }
    void readSettings(bridge)
    return () => { active = false }
  }, [bridge, settingsReadAttempt])

  async function applySettings(call: () => Promise<SettingsResult>) {
    if (settingsBusy) return false
    setSettingsBusy(true)
    setSettingsMessage('')
    try {
      const result = await call()
      if (!result.ok) {
        setSettingsMessage(settingsErrorText(result.error))
        return false
      }
      setSettings(result.settings)
      savedEvaluationMode.current = { mode: result.evaluation_mode }
      setState((current) => current?.status === 'ready' ? { ...current, evaluation_mode: result.evaluation_mode } : current)
      return true
    } catch {
      setSettingsMessage('설정을 처리하지 못했습니다.')
      return false
    } finally {
      setSettingsBusy(false)
    }
  }

  async function saveKey(provider: ProviderId) {
    const key = keyDrafts[provider]
    if (!key.trim() || !bridge) return
    if (await applySettings(() => bridge.save_api_key(provider, key))) {
      setKeyDrafts((drafts) => drafts[provider] === key ? { ...drafts, [provider]: '' } : drafts)
    }
  }

  const providerSettings = settingsDraft ?? settings

  async function saveProviderSettings() {
    if (!bridge || !providerSettings) return
    if (await applySettings(() => bridge.save_provider_settings(providerSettings.provider, providerSettings.models))) {
      setSettingsDraft((current) => current === settingsDraft ? undefined : current)
    }
  }

  useEffect(() => {
    if (!bridge) return
    let active = true
    void Promise.resolve().then(async () => {
      if (!active) return
      try {
        const data = await bridge.learning_data()
        if (active) setLibrary(indexLibrary(data))
      } catch {
        if (active) setLibraryError(true)
      }
    })
    return () => { active = false }
  }, [bridge, libraryAttempt])

  function retryLibrary() {
    if (!bridge || !libraryError || library) return
    setLibraryError(false)
    setLibraryAttempt(libraryAttempt + 1)
  }

  useEffect(() => {
    if (!bridge || !library) return
    let active = true
    const all = new Set(library.concepts.map(({ id }) => id))
    const modeAtRequestStart = savedEvaluationMode.current
    bridge.set_interview_scope([...all]).then((initialState) => {
      if (!active) return
      appliedScope.current = requestedScope.current = all
      setSelected(all)
      acceptInterviewState(initialState, modeAtRequestStart)
    }).catch(() => {
      if (active) setInitialScopeError(true)
    })
    return () => { active = false }
  }, [acceptInterviewState, bridge, initialScopeAttempt, library])

  async function run(action: Exclude<Operation, 'scope'>, call: (recordStart: number) => Promise<InterviewState>) {
    if (pending.current && !(action === 'end' && pending.current === 'evaluate')) return
    const id = ++requestId.current
    pending.current = action
    setOperation(action)
    setBridgeError('')
    try {
      // The last known record can still change, including when End overtakes an evaluation.
      const records = state?.records ?? []
      const recordStart = action === 'redraw' ? 0 : Math.max(0, records.length - 1)
      const modeAtRequestStart = savedEvaluationMode.current
      const next = await call(recordStart)
      if (id !== requestId.current) return
      acceptInterviewState({ ...next, records: [...records.slice(0, recordStart), ...next.records] }, modeAtRequestStart)
      if (next.submission_blocked) setBridgeError(next.submission_blocked)
      else if (next.status !== 'error') setAnswerVersion((version) => version + 1)
    } catch {
      if (id === requestId.current) setBridgeError('작업을 완료하지 못했습니다. 다시 시도해 주세요.')
    } finally {
      if (id === requestId.current) {
        pending.current = undefined
        setOperation(undefined)
      }
    }
  }

  const finished = state?.status === 'finished'
  const hasFollowUp = state?.records.some((record) => record.concept_id === state.concept_id && record.turns.length > 0) ?? false
  const scopeLocked = !!state && state.status !== 'ready' && state.status !== 'empty'
  const scopeDisabled = !state || scopeLocked || (!!operation && operation !== 'scope')
  const conceptEntries = library?.concepts ?? []
  const learningIndex = Math.max(0, conceptEntries.findIndex(({ id }) => id === learningConcept))
  const currentLearningConcept = conceptEntries[learningIndex]
  const nextLearningConcept = conceptEntries[learningIndex + 1] ?? conceptEntries[0]

  async function toggleScope(ids: string[]) {
    if (!bridge || !state || scopeLocked || (pending.current && pending.current !== 'scope')) return

    const next = new Set(requestedScope.current)
    const remove = ids.every((id) => next.has(id))
    for (const id of ids) {
      if (remove) next.delete(id)
      else next.add(id)
    }
    requestedScope.current = next
    setSelected(next)
    if (pending.current === 'scope') return

    pending.current = 'scope'
    setOperation('scope')
    setBridgeError('')
    const previousScope = appliedScope.current
    // Serialize bridge calls because each call mutates the session's question.
    let appliedState: InterviewState | undefined
    let appliedModeAtRequestStart: typeof savedEvaluationMode.current
    try {
      let target: ReadonlySet<string>
      do {
        await new Promise<void>((resolve) => window.setTimeout(resolve, scopeBatchDelayMs))
        target = requestedScope.current
        if (sameScope(target, appliedScope.current)) break
        try {
          const modeAtRequestStart = savedEvaluationMode.current
          appliedState = await bridge.set_interview_scope([...target])
          appliedModeAtRequestStart = modeAtRequestStart
          appliedScope.current = target
        } catch {
          if (target === requestedScope.current) {
            requestedScope.current = appliedScope.current
            setSelected(appliedScope.current)
            setBridgeError('출제 범위를 변경하지 못했습니다.')
            break
          }
        }
      } while (target !== requestedScope.current)
    } finally {
      if (appliedState) {
        acceptInterviewState(appliedState, appliedModeAtRequestStart)
        if (!sameScope(previousScope, appliedScope.current) ||
            state.question !== appliedState.question || state.concept_id !== appliedState.concept_id) {
          setAnswerVersion((version) => version + 1)
        }
      }
      pending.current = undefined
      setOperation(undefined)
    }
  }

  function drawLearningConcept() {
    const candidates = conceptEntries.filter(({ id }) => selected.has(id))
    if (!candidates.length) return
    const pool = candidates.length > 1 ? candidates.filter(({ id }) => id !== currentLearningConcept?.id) : candidates
    setLearningConcept(pool[Math.floor(Math.random() * pool.length)].id)
    setSidebarOpen(false)
    window.scrollTo(0, 0)
  }

  const libraryLoadError = (
    <>
      <p role="alert" className="text-rose-800">학습 자료를 불러오지 못했습니다.</p>
      <button type="button" className="interview-button" onClick={retryLibrary}>학습 자료 다시 불러오기</button>
    </>
  )

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="app-brand">
          <button type="button" className="icon-button sidebar-toggle" onClick={() => setSidebarOpen(!sidebarOpen)} aria-controls="topic-sidebar" aria-expanded={sidebarOpen} aria-label="주제 메뉴">
            <Icon name="menu" />
          </button>
          <span className="brand-mark"><Icon name="message" /></span>
          <span className="brand-name">CS 기술 면접</span>
        </div>
        <nav className="mode-switch" aria-label="앱 화면">
          <button type="button" aria-pressed={activeTab === 'interview'} onClick={() => setActiveTab('interview')}>면접 모드</button>
          <button type="button" aria-pressed={activeTab === 'learn'} onClick={() => setActiveTab('learn')}>공부 모드</button>
        </nav>
      </header>

      <dialog ref={settingsDialog} aria-labelledby="settings-title" className="settings-dialog" onClick={(event) => {
        if (event.target === event.currentTarget) event.currentTarget.close()
      }}>
        <section className="settings-content space-y-5">
          <div className="mb-6 flex items-center justify-between gap-3">
            <h2 id="settings-title" className="text-lg font-semibold">AI 모델 설정</h2>
            <button type="button" className="icon-button" autoFocus onClick={() => settingsDialog.current?.close()} aria-label="설정 닫기"><Icon name="x" /></button>
          </div>
          {settings && providerSettings ? <>
            <fieldset>
              <legend className="mb-2 text-sm font-medium text-slate-700">다음 면접 제공자</legend>
              <div className="grid grid-cols-2 gap-2">
                {(['gemini', 'openai'] as const).map((provider) => (
                  <label className="provider-card" key={provider}>
                    <input type="radio" name="provider" value={provider} checked={providerSettings.provider === provider} onChange={() => setSettingsDraft({ provider, models: providerSettings.models })} />
                    <span className="block text-sm font-semibold">{provider === 'gemini' ? 'Gemini' : 'Codex'}</span>
                    <span className="mt-1 block text-xs text-slate-500">{provider === 'gemini' ? 'Google AI' : 'OpenAI'}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="grid gap-3">
              {(['gemini', 'openai'] as const).map((provider) => <label key={provider} className="block text-sm font-semibold">
                {provider === 'gemini' ? 'Gemini 모델' : 'OpenAI 모델'}
                <input className="settings-input" value={providerSettings.models[provider]} maxLength={120} onChange={(event) => setSettingsDraft({ provider: providerSettings.provider, models: { ...providerSettings.models, [provider]: event.target.value } })} />
              </label>)}
            </div>
            <div className="grid gap-3">
              {(['gemini', 'openai'] as const).map((provider) => <div key={provider} className="rounded-xl border border-slate-200 p-4">
                <h3 className="font-semibold">{provider === 'gemini' ? 'Gemini API 키' : 'OpenAI API 키'}</h3>
                <p className="mt-2 text-[11px] leading-5 text-slate-500">{settings.key_registered[provider] ? '키 등록됨 · 입력하면 키를 교체합니다.' : '키가 등록되지 않았습니다.'}</p>
                <label className="mt-3 block text-sm font-medium" htmlFor={`${provider}-key`}>새 키</label>
                <input id={`${provider}-key`} type="password" autoComplete="new-password" value={keyDrafts[provider]} onChange={(event) => setKeyDrafts({ ...keyDrafts, [provider]: event.target.value })} className="settings-input" placeholder="저장 후 다시 표시되지 않습니다." />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="interview-button primary" disabled={settingsBusy || !keyDrafts[provider].trim()} onClick={() => void saveKey(provider)}>{settings.key_registered[provider] ? '키 교체' : '키 등록'}</button>
                  <button type="button" className="interview-button" disabled={settingsBusy || !settings.key_registered[provider]} onClick={() => void applySettings(() => bridge!.delete_api_key(provider))}>키 삭제</button>
                </div>
              </div>)}
            </div>
            <button type="button" className="settings-save" disabled={settingsBusy} onClick={() => void saveProviderSettings()}>제공자·모델 저장</button>
          </> : settingsReadError ? (
            <div className="space-y-2">
              <p role="alert" className="text-sm text-rose-800">{settingsReadError}</p>
              <button type="button" className="interview-button" onClick={() => {
                setSettingsReadError('')
                setSettingsReadAttempt((attempt) => attempt + 1)
              }}>설정 다시 불러오기</button>
            </div>
          ) : <p role="status">{bridge ? '설정을 불러오고 있습니다.' : '데스크톱 연결을 준비하고 있습니다.'}</p>}
          <button type="button" className="interview-button" disabled={!bridge || settingsBusy} onClick={async () => {
            if (!bridge) return
            setSettingsBusy(true)
            setSettingsMessage('')
            try {
              const result = await bridge.open_knowledge_folder()
              if (!result.ok) setSettingsMessage(settingsErrorText(result.error ?? 'folder_open_failed'))
            } catch { setSettingsMessage('지식 폴더를 열지 못했습니다.') }
            finally { setSettingsBusy(false) }
          }}>지식 폴더 열기</button>
          {settingsMessage && <p role="alert" className="text-sm text-rose-800">{settingsMessage}</p>}
        </section>
      </dialog>

      {sidebarOpen && <button type="button" className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} aria-label="주제 메뉴 닫기" />}
      <aside id="topic-sidebar" className="topic-sidebar" data-open={sidebarOpen} aria-label="면접 주제">
        <div className="sidebar-heading">
          <h2 className="text-sm font-semibold">면접 주제</h2>
          <button type="button" className="icon-button sidebar-toggle" onClick={() => setSidebarOpen(false)} aria-label="주제 메뉴 닫기"><Icon name="x" /></button>
        </div>
        <div className="scope-count"><span>선택된 개념</span><span>{selected.size}</span></div>
        <div className="topic-scroll">
          {!library && (libraryError
            ? <p role="alert">학습 자료를 불러오지 못했습니다.</p>
            : <p role="status">학습 자료를 불러오고 있습니다.</p>)}
          {library && !library.topics.length && <p role="status">사용할 수 있는 지식 데이터가 없습니다. 지식 폴더에 유효한 JSON 파일을 추가한 뒤 앱을 다시 시작해 주세요.</p>}
          {library && library.errors.length > 0 && <div role="alert" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-950">
            <p className="font-semibold">읽지 못한 지식 파일</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">{library.errors.map((error, index) => <li key={`${error.filename}-${index}`}><strong>{error.filename}</strong>: {error.detail}</li>)}</ul>
          </div>}
          {library && library.topics.length > 0 && <div>
            {library.topics.map(({ topic, units, ids: topicIds }) => {
              const topicCount = topicIds.filter((id) => selected.has(id)).length
              const topicKey = `topic:${topic.id}`
              return <section className="mb-1" key={topic.id}>
                <div className="tree-row">
                  <input aria-label={`${topic.name} 전체 출제 범위`} type="checkbox" checked={topicCount === topicIds.length} ref={(element) => { if (element) element.indeterminate = topicCount > 0 && topicCount < topicIds.length }} disabled={scopeDisabled} onChange={() => void toggleScope(topicIds)} />
                  <button className="tree-branch" aria-expanded={openUnits.includes(topicKey)} onClick={() => setOpenUnits((open) => open.includes(topicKey) ? open.filter((id) => id !== topicKey) : [...open, topicKey])}><Icon name="chevron" />{topic.name}</button>
                </div>
                {openUnits.includes(topicKey) && units.map(({ unit, ids, key: unitKey, concepts }) => {
                  const count = ids.filter((id) => selected.has(id)).length
                  return <div className="tree-children" key={unitKey}>
                    <div className="tree-row"><input aria-label={`${unit.name} 전체 출제 범위`} type="checkbox" checked={count === ids.length} ref={(element) => { if (element) element.indeterminate = count > 0 && count < ids.length }} disabled={scopeDisabled} onChange={() => void toggleScope(ids)} />
                      <button className="tree-branch" aria-expanded={openUnits.includes(unitKey)} onClick={() => setOpenUnits((open) => open.includes(unitKey) ? open.filter((id) => id !== unitKey) : [...open, unitKey])}><Icon name="chevron" />{unit.name}</button></div>
                    {openUnits.includes(unitKey) && concepts.map(({ id, concept }) => {
                      return <div className="tree-row tree-leaf" key={id}>
                        <input aria-label={`${concept.name} 출제 범위`} type="checkbox" checked={selected.has(id)} disabled={scopeDisabled} onChange={() => void toggleScope([id])} />
                        <button type="button" onClick={() => { setLearningConcept(id); setActiveTab('learn'); setSidebarOpen(false) }} aria-pressed={currentLearningConcept?.id === id}>{concept.name}<span className="tree-question-count" aria-label={`질문 ${concept.questions.length}개`}>{concept.questions.length}</span></button>
                      </div>
                    })}
                  </div>
                })}
              </section>
            })}
          </div>}
          {scopeLocked && <p className="text-sm text-slate-600">면접이 시작되어 출제 범위가 고정되었습니다.</p>}
          {state && library && library.topics.length > 0 && !selected.size && <p role="status">출제 범위가 비어 있습니다. 면접 질문을 선택할 수 없습니다.</p>}
          {activeTab === 'learn' ? (
            <div className="flex justify-center">
              <button type="button" className="interview-button primary" disabled={!selected.size} onClick={drawLearningConcept}>개념 뽑기</button>
            </div>
          ) : state?.status === 'ready' && selected.size > 0 && (
            <div className="flex justify-center">
              <button type="button" className="interview-button primary" disabled={!!operation} onClick={() => { setSidebarOpen(false); void run('redraw', () => bridge!.redraw_question()) }}>질문 뽑기</button>
            </div>
          )}
        </div>
        <div className="sidebar-footer">
          <button type="button" className="settings-trigger" onClick={() => settingsDialog.current?.showModal()}><Icon name="gear" /> 설정</button>
        </div>
      </aside>

      <main className="workspace" aria-label={activeTab === 'learn' ? '공부 모드' : '면접 모드'}>
        <div className="workspace-inner">
          {activeTab === 'learn' && (
            <>
              {bridgeError && <p role="alert" className="mb-4 text-rose-800">{bridgeError}</p>}
              {currentLearningConcept ? (
                <div className="space-y-5">
                    {currentLearningConcept.concept.questions.map((question) => (
                      <section key={question.id} className="panel">
                        <div className="question-header">
                          <div className="question-meta">
                            <span className="question-label">기본 질문</span>
                            <span title={`${currentLearningConcept.topic.name} · ${currentLearningConcept.unit.name}`}>{currentLearningConcept.concept.name}</span>
                          </div>
                          <h2 className="question-text">{question.prompt}</h2>
                        </div>
                        <div className="panel-body">
                          <article className="study-answer">
                            <h3 className="mb-3 border-l-2 border-blue-600 pl-3 text-sm font-semibold text-slate-800">예시 답안</h3>
                            <p className="whitespace-pre-wrap break-words text-base leading-8 text-slate-700">{question.example_answer}</p>
                          </article>
                        </div>
                      </section>
                    ))}
                    <div className="flex justify-end">
                      <button type="button" className="interview-button primary" onClick={() => {
                        setLearningConcept(nextLearningConcept.id)
                        window.scrollTo(0, 0)
                      }}>다음 개념</button>
                    </div>
                </div>
              ) : <section className="panel panel-empty space-y-3">
                {libraryError
                  ? libraryLoadError
                  : <p role="status">{library ? '학습할 개념이 없습니다.' : '학습 자료를 불러오고 있습니다.'}</p>}
              </section>}
            </>
          )}

          <div hidden={activeTab !== 'interview'} className="space-y-5">
            <section className="panel">
              {bridgeError && <p role="alert" className="px-6 py-4 text-rose-800">{bridgeError}</p>}
              {!state && (initialScopeError ? (
                <div className="panel-body space-y-3">
                  <p role="alert" className="text-rose-800">초기 출제 범위를 적용하지 못했습니다.</p>
                  <button type="button" className="interview-button" onClick={() => {
                    setInitialScopeError(false)
                    setInitialScopeAttempt((attempt) => attempt + 1)
                  }}>출제 범위 다시 적용</button>
                </div>
              ) : libraryError ? (
                <div className="panel-body space-y-3">{libraryLoadError}</div>
              ) : (
                <p role="status" className="panel-empty">
                  {library ? '초기 출제 범위를 적용하고 있습니다.' : bridge ? '학습 자료를 불러오고 있습니다.' : '데스크톱 연결을 준비하고 있습니다.'}
                </p>
              ))}
              {finished && (
                <div role="status" className="panel-body">
                  <h2 className="text-xl font-semibold">면접이 종료되었습니다.</h2>
                  <p className="mt-2 text-slate-600">면접 기록에서 답변과 공개된 평가를 확인할 수 있습니다.</p>
                </div>
              )}
              {state?.status === 'empty' && <p role="status" className="panel-empty">{library && library.topics.length === 0 ? '사용할 수 있는 지식 데이터가 없습니다. 학습 화면에서 지식 파일 오류를 확인하거나 유효한 JSON 파일을 추가해 주세요.' : '출제 범위가 비어 있어 질문을 뽑을 수 없습니다.'}</p>}
              {bridge && state?.question && !finished && (
                <>
                  <div className="question-header">
                    <div className="question-meta">
                      <span className="question-label">{hasFollowUp ? '꼬리 질문' : '면접 질문'}</span>
                      <span>{state.concept_name}</span>
                    </div>
                    <h2 className="question-text">{state.question}</h2>
                  </div>
                  <AnswerForm
                    key={answerVersion}
                    state={state}
                    operation={operation}
                    hasFollowUp={hasFollowUp}
                    onSubmit={(draft) => {
                      if (state.status === 'error') void run('evaluate', bridge.retry_answer)
                      else void run('evaluate', (recordStart) => bridge.submit_answer(draft, state.evaluation_mode, recordStart))
                    }}
                    onRedraw={() => void run('redraw', () => bridge.redraw_question())}
                    onNext={() => void run('next', bridge.next_concept)}
                    onEnd={() => void run('end', bridge.end_interview)}
                  />
                </>
              )}
            </section>

            {activeTab === 'interview' && <aside className="panel panel-body space-y-4">
              <h2 className="text-lg font-semibold">면접 기록</h2>
              {state?.records.map((record) => (record.turns.length > 0 || record.unanswered_questions.length > 0) && (
                <InterviewRecord key={record.concept_id} record={record} />
              ))}
            </aside>}
          </div>
        </div>
      </main>
    </div>
  )
}

function settingsErrorText(code: SettingsErrorCode): string {
  const messages: Record<SettingsErrorCode, string> = {
    dpapi_unavailable: '이 기능은 Windows에서만 사용할 수 있습니다.',
    decrypt_failed: '저장된 키를 복호화하지 못했습니다. 현재 사용자 계정과 키 파일을 확인하세요.',
    encrypt_failed: '키를 보호하지 못했습니다. 기존 저장 데이터는 유지되었습니다.',
    key_file_unavailable: '키 파일을 읽지 못했습니다. 파일 권한과 상태를 확인하세요.',
    settings_unavailable: '설정 파일을 읽지 못했습니다.',
    invalid_settings: '제공자 또는 모델 설정을 확인해 주세요.',
    invalid_key: '저장할 키를 입력해 주세요.',
    invalid_provider: '지원하지 않는 제공자입니다.',
    write_failed: '파일을 저장하지 못했습니다. 기존 저장 데이터는 유지되었습니다.',
    folder_open_failed: '지식 폴더를 열지 못했습니다.',
  }
  return messages[code]
}
