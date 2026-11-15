import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { ConceptRecord, InterviewBridge, InterviewState, KnowledgeLibraryData, ProviderId, QuestionGeneration, SettingsData, SettingsErrorCode, SettingsResult, TtsBridge } from './bridge'
import KnowledgeAuthoring from './KnowledgeAuthoring'
import MicrophoneSettings from './MicrophoneSettings'
import useSpeechReading from './useSpeechReading'
import useVoiceAnswer from './useVoiceAnswer'

declare global {
  interface Window {
    pywebview?: { api?: InterviewBridge & Partial<TtsBridge> }
  }
}

type IconName =
  | "chevron"
  | "file-plus"
  | "gear"
  | "menu"
  | "message"
  | "send"
  | "x";

function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, ReactNode> = {
    chevron: <path d="m9 18 6-6-6-6" />,
    "file-plus": <path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10m-7-7 7 7m-7-7v7h7m-8 3v5m-2.5-2.5h5" />,
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
      {record.skipped && <p className="mt-1 text-sm text-slate-600">건너뛴 개념</p>}
      {record.turns.map(({ question, answer, evaluation }, index) => (
        <div className="mt-4 space-y-3 text-sm leading-6" key={index}>
          <p className="font-medium text-slate-700">{question}</p>
          <div className="border-t border-slate-100 pt-3">
            <p className="mb-1 font-medium text-slate-600">나의 답변</p>
            <p className="whitespace-pre-wrap break-words text-slate-800">{answer}</p>
          </div>
          {evaluation && (
            <div className="border-t border-slate-100 pt-3">
            <p className="mb-2 font-medium text-slate-600">평가</p>
            <dl className="space-y-3 text-slate-700">
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
            </div>
          )}
        </div>
      ))}
      {record.unanswered_questions.map((question) => (
        <p className="mt-3 text-slate-600" key={question}>{question} <span className="font-medium">· 미응답</span></p>
      ))}
    </section>
  )
})

type Operation = 'evaluate' | 'generate' | 'redraw' | 'next' | 'end' | 'scope' | 'restart' | 'sync' | 'reload'

function ReadingControls({ status, playLabel, stopLabel, play, stop, disabled, showBusyStatus = true }: {
  status: NonNullable<ReturnType<typeof useSpeechReading>['reading']>['status'] | undefined
  playLabel: string
  stopLabel: string
  play: () => void
  stop: () => void
  disabled?: boolean
  showBusyStatus?: boolean
}) {
  const busy = status === 'synthesizing' || status === 'playing'
  return (
    <div className="reading-controls">
      <button type="button" className="interview-button" disabled={disabled} onClick={busy ? stop : play}>
        {busy ? stopLabel : playLabel}
      </button>
      {status === 'failed'
        ? <p role="alert" className="text-sm text-rose-800">음성을 재생하지 못했습니다.</p>
        : <p role="status" className="sr-only">
          {status === 'completed' && '읽기 완료'}
          {status === 'stopped' && '읽기 중지됨'}
        </p>}
      {busy && showBusyStatus && <span role="status" className="text-sm text-slate-600">
        {status === 'playing' ? '읽는 중' : '음성 준비 중'}
      </span>}
    </div>
  )
}
type EvaluationMode = Pick<InterviewState, 'evaluation_mode' | 'question_generation_available'>
type InterviewRequest = {
  action: Exclude<Operation, 'scope' | 'sync'>
  response: Promise<InterviewState>
  recordPrefix: ConceptRecord[]
  modeAtStart: EvaluationMode | undefined
}
const scopeBatchDelayMs = 75

function sameScope(left: ReadonlySet<string>, right: ReadonlySet<string>) {
  if (left.size !== right.size) return false
  for (const id of left) {
    if (!right.has(id)) return false
  }
  return true
}

type AnswerFormProps = {
  draft: string
  voiceBusy: boolean
  readingControls?: React.ReactNode
  voiceControls?: React.ReactNode
  onStart?: () => void
  onDraftChange: (draft: string) => void
  syncFailed: boolean
  state: InterviewState
  operation?: Operation
  hasFollowUp: boolean
  generationProgress: QuestionGeneration | null
  onSubmit: (draft: string) => void
  onRedraw: () => void
  onNext: () => void
  onEnd: () => void
}

function AnswerForm({ draft, voiceBusy, readingControls, voiceControls, onStart, onDraftChange, state, operation, hasFollowUp, generationProgress, syncFailed, onSubmit, onRedraw, onNext, onEnd }: AnswerFormProps) {
  const starting = state.status === 'ready' && !state.question
  const generating = !!generationProgress || operation === 'generate' || state.status === 'generating_question'
  const evaluating = !generating && (operation === 'evaluate' || state.busy)
  const failedAnswer = state.status === 'error'
  const failedQuestion = state.status === 'question_error'
  const unavailable = state.status === 'question_unavailable' || (starting && state.question_generation_available === false)
  const failed = failedAnswer || failedQuestion
  const canSubmit = !voiceBusy && !syncFailed && !operation && !evaluating && !generating && !unavailable && (failed || !!draft.trim())

  return (
    <form className="answer-form" onSubmit={(event) => {
      event.preventDefault()
      if (canSubmit) onSubmit(draft)
    }}>
      {!generating && (starting || state.question) && (
        <label className="mb-2 block text-xs font-medium text-slate-600" htmlFor="answer">{starting ? '시작 메시지' : '나의 답변'}</label>
      )}
      <div className="answer-box">
        {!generating && (starting || state.question) && <textarea
          id="answer"
          value={draft}
          onChange={(event) => onDraftChange(event.target.value)}
          readOnly={!!operation || evaluating || failed || unavailable}
          rows={4}
          placeholder={starting ? '시작 메시지를 입력하세요' : '답변을 입력하세요'}
          className="block min-h-24 w-full resize-y bg-transparent px-1 py-1 text-sm leading-6 text-slate-700 outline-none placeholder:text-slate-400"
        />}
        {generating && <p role="status" className="text-blue-800">첫 면접 질문을 생성하고 있습니다.</p>}
        {operation === 'next' && !generating && <p role="status" className="text-blue-800">다음 개념으로 이동하고 있습니다.</p>}
        {evaluating && <p role="status" className="text-blue-800">답변을 평가하고 있습니다.</p>}
        {!generating && !evaluating && (failed || unavailable) && (
          <p role="alert" className="text-rose-800">
            {state.error ?? '첫 질문을 생성할 수 있는 AI 제공자를 설정해 주세요.'}
            {failedAnswer && ' 같은 답변을 재시도하거나 면접을 종료할 수 있습니다.'}
          </p>
        )}
      </div>
      <div className="answer-navigation">
          {onStart && <button type="button" className="interview-button primary" disabled={syncFailed || !!operation} onClick={onStart}>시작</button>}
          {state.status === 'ready' && !generating && (
            <button type="button" className="interview-button" disabled={syncFailed || !!operation} onClick={onRedraw}>
              새 질문 선택
            </button>
          )}
          {(hasFollowUp || generationProgress || failedQuestion || state.status === 'question_unavailable') && (
            <button type="button" className="interview-button" disabled={syncFailed || operation === 'sync' || (!generationProgress && (!!operation || evaluating)) || failedAnswer} onClick={onNext}>
              {generationProgress || failedQuestion || state.status === 'question_unavailable' ? '개념 건너뛰기' : '다음 개념'}
            </button>
          )}
          {readingControls}
          {voiceControls}
          <button type="button" className="interview-button" disabled={syncFailed || operation === 'sync' || operation === 'scope' || operation === 'redraw' || operation === 'end'} onClick={onEnd}>
            면접 종료
          </button>
          <button className="interview-button primary submit-answer" disabled={!canSubmit}
            aria-describedby={state.evaluation_mode === 'gemini' && canSubmit
              ? starting ? 'gemini-generation-notice' : 'gemini-transfer-notice'
              : undefined}>
            {failed ? '재시도' : starting ? '면접 시작' : '답변 제출'} <Icon name="send" />
          </button>
      </div>
    </form>
  )
}

function InterviewFooter({ state, operation, generationProgress }: Pick<AnswerFormProps, 'state' | 'operation' | 'generationProgress'>) {
  if (state.evaluation_mode !== 'gemini') return null

  const ready = state.status === 'ready'
  const showTransfer = !(ready && !state.question) && !generationProgress && !state.busy &&
    operation !== 'generate' && operation !== 'evaluate' &&
    state.status !== 'generating_question' && state.status !== 'question_unavailable'
  if (!ready && !showTransfer) return null

  return (
    <footer className="mt-auto space-y-1 pt-8 text-center text-xs leading-5 text-slate-600">
      {ready && <p id="gemini-generation-notice">저장된 질문이 없으면 주제·단원·개념 이름을 Gemini로 보냅니다.</p>}
      {showTransfer && <p id="gemini-transfer-notice">{state.status === 'question_error'
        ? '첫 질문 생성에 주제·단원·개념 이름을 Gemini로 보냅니다.'
        : '평가에 현재 및 같은 개념의 이전 문답을 Gemini로 보냅니다.'}</p>}
    </footer>
  )
}

function countSelected(ids: readonly string[], selected: ReadonlySet<string>) {
  let count = 0
  for (const id of ids) {
    if (selected.has(id)) count++
  }
  return count
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
  const studyTopics = topics.map((entry) => ({
    ...entry,
    units: entry.units.map((unitEntry) => ({
      ...unitEntry,
      concepts: unitEntry.concepts.flatMap((conceptEntry) => {
        const questions = conceptEntry.concept.questions.filter((question) => question.example_answer != null)
        return questions.length ? [{ ...conceptEntry, concept: { ...conceptEntry.concept, questions } }] : []
      }),
    })).filter(({ concepts }) => concepts.length > 0),
  })).filter(({ units }) => units.length > 0)
  const studyConcepts = studyTopics.flatMap(({ units }) => units.flatMap(({ concepts }) => concepts))
  const studyIndexById = new Map<string, number>()
  studyConcepts.forEach(({ id }, index) => studyIndexById.set(id, index))
  return {
    topics,
    studyTopics,
    errors: data.errors,
    concepts: topics.flatMap(({ units }) => units.flatMap(({ concepts }) => concepts)),
    studyConcepts,
    studyIndexById,
  }
}

export default function App() {
  const [bridge, setBridge] = useState(window.pywebview?.api)
  const [state, setState] = useState<InterviewState>()
  const answerDraft = useRef('')
  const [draft, setDraft] = useState('')
  const updateAnswerDraft = useCallback((value: string) => {
    answerDraft.current = value
    setDraft(value)
  }, [])
  const clearAnswerDraft = useCallback(() => updateAnswerDraft(''), [updateAnswerDraft])
  const [previousDrafts, setPreviousDrafts] = useState<{ question: string; answer: string }[]>([])
  const syncDraftOrigin = useRef<InterviewState | undefined>(undefined)
  const [operation, setOperation] = useState<Operation>()
  const [bridgeError, setBridgeError] = useState('')
  const [syncFailed, setSyncFailed] = useState(false)
  const settingsDialog = useRef<HTMLDialogElement>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [settings, setSettings] = useState<SettingsData>()
  const [settingsReadError, setSettingsReadError] = useState('')
  const [settingsReadAttempt, setSettingsReadAttempt] = useState(0)
  const [settingsDraft, setSettingsDraft] = useState<Pick<SettingsData, 'provider' | 'models'>>()
  const [settingsMessage, setSettingsMessage] = useState('')
  const [keyDrafts, setKeyDrafts] = useState<Record<ProviderId, string>>({ gemini: '', openai: '' })
  const [settingsBusy, setSettingsBusy] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsTab, setSettingsTab] = useState<'sound' | 'api'>('sound')
  const soundSettingsOpen = settingsOpen && settingsTab === 'sound'
  const [outputDraft, setOutputDraft] = useState<{ deviceId: string | null; volume: number }>()
  const [outputDevices, setOutputDevices] = useState<{ id: string; name: string }[]>()
  const [outputDevicesError, setOutputDevicesError] = useState(false)
  const [outputDevicesAttempt, setOutputDevicesAttempt] = useState(0)
  const pendingSpeechStart = useRef(Promise.resolve())

  useEffect(() => {
    if (!soundSettingsOpen) return
    let active = true
    setOutputDevicesError(false)
    async function loadDevices() {
      try {
        const result = await bridge?.tts_devices?.()
        if (active && result?.ok) setOutputDevices(result.devices)
        else if (active) setOutputDevicesError(true)
      } catch {
        if (active) setOutputDevicesError(true)
      }
    }
    void loadDevices()
    return () => { active = false }
  }, [bridge, soundSettingsOpen, outputDevicesAttempt])
  const [library, setLibrary] = useState<ReturnType<typeof indexLibrary>>()
  const [libraryError, setLibraryError] = useState(false)
  const [libraryAttempt, setLibraryAttempt] = useState(0)
  const [initialScopeError, setInitialScopeError] = useState(false)
  const [initialScopeAttempt, setInitialScopeAttempt] = useState(0)
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set())
  const [openBranches, setOpenBranches] = useState<ReadonlySet<string>>(() => new Set())
  const [learningConcept, setLearningConcept] = useState<string>()
  const [activeTab, setActiveTab] = useState<'learn' | 'interview' | 'authoring'>('learn')
  const [interviewStarted, setInterviewStarted] = useState(false)
  const lastAutoRead = useRef<number>(undefined)
  const pending = useRef<Operation | undefined>(undefined)
  const reloadRequest = useRef<{ id: string; active: boolean } | undefined>(undefined)
  const [reloadPending, setReloadPending] = useState(false)
  const [reloadFailed, setReloadFailed] = useState(false)
  const latestInterview = useRef<InterviewState | undefined>(undefined)
  const requestId = useRef(0)
  const skippedGeneration = useRef<number | undefined>(undefined)
  const inFlightRequest = useRef<InterviewRequest | undefined>(undefined)
  const canPollGeneration = useRef(true)
  const [generationProgress, setGenerationProgress] = useState<QuestionGeneration | null>(null)
  const requestedScope = useRef(selected)
  const appliedScope = useRef(selected)
  const initializedScope = useRef(false)
  // Each successful settings write creates a token for requests that overlap the write.
  const savedEvaluationMode = useRef<EvaluationMode | undefined>(undefined)

  const acceptInterviewState = useCallback((next: InterviewState, modeAtRequestStart: typeof savedEvaluationMode.current) => {
    latestInterview.current = next
    const latest = savedEvaluationMode.current
    if (next.status === 'ready' && latest && latest !== modeAtRequestStart) {
      setState({ ...next, ...latest })
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
    if (settingsBusy || reloadRequest.current?.active) return false
    setSettingsBusy(true)
    setSettingsMessage('')
    try {
      const result = await call()
      if (!result.ok) {
        setSettingsMessage(settingsErrorText(result.error))
        return false
      }
      setSettings(result.settings)
      const mode = {
        evaluation_mode: result.evaluation_mode,
        question_generation_available: result.question_generation_available,
      }
      savedEvaluationMode.current = mode
      setState((current) => current?.status === 'ready' ? { ...current, ...mode } : current)
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
      const reloadAtStart = reloadRequest.current
      try {
        const data = await bridge.learning_data()
        if (active && !reloadAtStart && !reloadRequest.current && !initializedScope.current) setLibrary(indexLibrary(data))
      } catch {
        if (active && !reloadAtStart && !reloadRequest.current && !initializedScope.current) setLibraryError(true)
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
    if (!bridge || !library || initializedScope.current || reloadRequest.current?.active) return
    let active = true
    const all = new Set(library.concepts.map(({ id }) => id))
    const modeAtRequestStart = savedEvaluationMode.current
    let started = false
    void Promise.resolve().then(async () => {
      if (!active || pending.current || reloadRequest.current?.active) return
      started = true
      pending.current = 'scope'
      const initialState = await bridge.set_interview_scope([...all])
      if (!active) return
      initializedScope.current = true
      appliedScope.current = requestedScope.current = all
      setSelected(all)
      acceptInterviewState(initialState, modeAtRequestStart)
    }).catch(() => {
      if (active) setInitialScopeError(true)
    }).finally(() => {
      if (started) pending.current = undefined
    })
    return () => { active = false }
  }, [acceptInterviewState, bridge, initialScopeAttempt, library])

  function archiveAnswerDraft(origin: InterviewState | undefined) {
    const draft = answerDraft.current
    if (!origin || !draft) return
    setPreviousDrafts((current) => [...current, { question: origin.question ?? '면접 시작 메시지', answer: draft }])
    clearAnswerDraft()
  }

  function applyScope(scopeIds: string[]) {
    const scope = new Set(scopeIds)
    appliedScope.current = requestedScope.current = scope
    setSelected((current) => sameScope(current, scope) ? current : scope)
  }

  async function reloadKnowledge(retry = false) {
    if (!bridge || reloadRequest.current?.active) return
    voice.cancel()
    const request = { id: (retry && reloadRequest.current?.id) || crypto.randomUUID(), active: true }
    reloadRequest.current = request
    setReloadPending(true)
    setReloadFailed(false)
    try {
      while (pending.current) {
        await new Promise<void>((resolve) => window.setTimeout(resolve, 150))
      }
      const id = ++requestId.current
      pending.current = 'reload'
      setOperation('reload')
      const origin = latestInterview.current
      const mode = savedEvaluationMode.current
      try {
        const { library: data, interview } = await bridge.reload_knowledge(request.id)
        setLibrary(indexLibrary(data))
        setLibraryError(false)
        initializedScope.current = true
        applyScope(interview.scope_ids)
        preserveDraftAfterSync(syncDraftOrigin.current ?? origin, interview)
        syncDraftOrigin.current = undefined
        acceptInterviewState(interview, mode)
        setSyncFailed(false)
      } catch {
        syncDraftOrigin.current ??= origin
        if (!await syncInterview(id, request.id)) {
          setReloadFailed(true)
          return
        }
      }
      reloadRequest.current = undefined
    } finally {
      request.active = false
      pending.current = undefined
      setOperation(undefined)
      setReloadPending(false)
    }
  }

  useEffect(() => {
    if (activeTab !== 'interview' || !bridge?.question_generation || !canPollGeneration.current ||
      (operation !== 'evaluate' && operation !== 'generate' && operation !== 'next')) return
    let active = true
    let timer: number
    async function poll(api: InterviewBridge) {
      const id = requestId.current
      try {
        const progress = await api.question_generation()
        if (active && id === requestId.current && pending.current) {
          const next = progress?.token === skippedGeneration.current ? null : progress
          setGenerationProgress((current) =>
            current?.token === next?.token && current?.concept_name === next?.concept_name ? current : next)
        }
      } catch {
        // The operation's own response supplies its outcome.
      }
      if (active) timer = window.setTimeout(() => void poll(api), 150)
    }
    void poll(bridge)
    return () => {
      active = false
      window.clearTimeout(timer)
    }
  }, [activeTab, bridge, operation])

  function preserveDraftAfterSync(origin: InterviewState | undefined, snapshot: InterviewState) {
    if (origin && (
      snapshot.question_version !== origin.question_version ||
      snapshot.concept_id !== origin.concept_id || snapshot.question !== origin.question ||
      snapshot.status === 'finished' || snapshot.status === 'empty' ||
      snapshot.records.length > origin.records.length ||
      (snapshot.records.at(-1)?.turns.length ?? 0) > (origin.records.at(-1)?.turns.length ?? 0)
    )) archiveAnswerDraft(origin)
  }

  async function syncInterview(id: number, reloadId?: string): Promise<boolean> {
    if (!bridge) return false
    pending.current = 'sync'
    setOperation('sync')
    setGenerationProgress(null)
    try {
      while (id === requestId.current) {
        const busy = await bridge.interview_busy()
        if (id !== requestId.current) return false
        if (!busy) {
          const modeAtStart = savedEvaluationMode.current
          const { scope_ids, library: data, reload_id, ...snapshot } = await (
            reloadId === undefined ? bridge.interview_state() : bridge.interview_state(true)
          )
          if (id !== requestId.current) return false
          if (!snapshot.busy) {
            if (reloadId !== undefined) {
              if (!data) throw new Error('Knowledge library missing from interview snapshot')
              setLibrary(indexLibrary(data))
              setLibraryError(false)
              initializedScope.current = true
            }
            applyScope(scope_ids)
            preserveDraftAfterSync(syncDraftOrigin.current, snapshot)
            syncDraftOrigin.current = undefined
            acceptInterviewState(snapshot, modeAtStart)
            setSyncFailed(false)
            return reloadId === undefined || reloadId === reload_id
          }
        }
        await new Promise<void>((resolve) => window.setTimeout(resolve, 150))
      }
    } catch {
      if (id === requestId.current) setSyncFailed(true)
    }
    return false
  }

  async function retryInterviewSync() {
    if (!bridge || pending.current || reloadRequest.current?.active) return
    const id = ++requestId.current
    setBridgeError('')
    const reloadId = reloadRequest.current?.id
    if (await syncInterview(id, reloadId) && reloadId) {
      reloadRequest.current = undefined
      setReloadFailed(false)
    }
    if (id === requestId.current) {
      pending.current = undefined
      setOperation(undefined)
    }
  }

  async function run(action: Exclude<Operation, 'scope' | 'sync' | 'reload'>, call: (recordStart: number) => Promise<InterviewState>, generationToken?: number) {
    if (syncFailed || reloadRequest.current?.active) return
    if (generationToken !== undefined && generationToken === skippedGeneration.current) return
    const interruptible = pending.current === 'evaluate' || pending.current === 'generate' || pending.current === 'next'
    const canInterrupt = action === 'end' || generationToken !== undefined
    if (pending.current && !(interruptible && canInterrupt)) return
    const interrupted = canInterrupt ? inFlightRequest.current : undefined
    if (generationToken !== undefined) skippedGeneration.current = generationToken
    const id = ++requestId.current
    pending.current = action
    voice.cancel()
    if (action === 'restart') {
      setInterviewStarted(false)
      lastAutoRead.current = undefined
    }
    if (action === 'evaluate' || action === 'generate') setInterviewStarted(true)
    // Keep the capability from the operation's start even if settings change while it runs.
    canPollGeneration.current = state?.question_generation_available !== false
    setOperation(action)
    setGenerationProgress(null)
    setBridgeError('')
    function applyResponse(next: InterviewState, prefix: ConceptRecord[], modeAtStart: EvaluationMode | undefined) {
      syncDraftOrigin.current = undefined
      if ((action === 'restart' || next.question_changed) && next.scope_ids) applyScope(next.scope_ids)
      if (next.question_changed) archiveAnswerDraft(state)
      acceptInterviewState(next.question_changed ? next : { ...next, records: [...prefix, ...next.records] }, modeAtStart)
      if (next.submission_blocked) setBridgeError(next.submission_blocked)
      else if (next.status !== 'error') clearAnswerDraft()
    }

    try {
      // The last known record can still change, including when End overtakes an evaluation.
      const records = state?.records ?? []
      const recordStart = action === 'redraw' || action === 'restart' ? 0 : Math.max(0, records.length - 1)
      const modeAtStart = savedEvaluationMode.current
      const recordPrefix = records.slice(0, recordStart)
      const response = call(recordStart)
      inFlightRequest.current = { action, response, recordPrefix, modeAtStart }
      const next = await response
      if (id === requestId.current) {
        applyResponse(next, recordPrefix, modeAtStart)
        if (next.question_changed && next.busy) await syncInterview(id)
      }
    } catch {
      if (id !== requestId.current) return
      syncDraftOrigin.current ??= state
      if (generationToken !== undefined) skippedGeneration.current = undefined
      setBridgeError('작업 결과를 받지 못했습니다. 현재 면접 상태를 확인해 주세요.')
      if (interrupted) {
        inFlightRequest.current = interrupted
        pending.current = interrupted.action
        setOperation(interrupted.action)
        setGenerationProgress(generationProgress)
        const previous = await interrupted.response.catch(() => undefined)
        if (id !== requestId.current) return
        if (previous && !previous.busy) {
          acceptInterviewState({
            ...previous, records: [...interrupted.recordPrefix, ...previous.records],
          }, interrupted.modeAtStart)
        }
      }
      await syncInterview(id)
    } finally {
      if (id === requestId.current) {
        pending.current = undefined
        inFlightRequest.current = undefined
        setOperation(undefined)
        setGenerationProgress(null)
      }
    }
  }

  const finished = state?.status === 'finished'
  const lastRecord = state?.records.at(-1)
  const hasFollowUp = !generationProgress && operation !== 'generate' && state?.status !== 'generating_question' &&
    !!lastRecord && lastRecord.concept_id === state?.concept_id && lastRecord.turns.length > 0
  const scopeLocked = !!state && state.status !== 'ready' && state.status !== 'empty' && !finished
  const scopeDisabled = reloadPending || syncFailed || !state || scopeLocked || (!!operation && operation !== 'scope')
  const studying = activeTab === 'learn'
  const sidebarLabel = studying ? '학습 주제' : '면접 주제'
  const sidebarTopics = studying ? library?.studyTopics : library?.topics
  const interviewTopics = activeTab === 'interview' ? library?.topics : undefined
  const selectedCounts = useMemo(() => {
    if (!interviewTopics) return undefined
    const counts = new Map<string, number>()
    for (const { topic, units } of interviewTopics) {
      let topicCount = 0
      for (const { key, ids } of units) {
        const count = countSelected(ids, selected)
        counts.set(key, count)
        topicCount += count
      }
      counts.set(topic.id, topicCount)
    }
    return counts
  }, [interviewTopics, selected])
  const studyConcepts = library?.studyConcepts
  const learningCandidates = useMemo(() => studyConcepts?.filter(({ id }) => selected.has(id)) ?? [], [studyConcepts, selected])
  const learningIndex = learningConcept ? library?.studyIndexById.get(learningConcept) ?? 0 : 0
  const currentLearningConcept = studyConcepts?.[learningIndex]
  const nextLearningConcept = studyConcepts?.[learningIndex + 1] ?? studyConcepts?.[0]
  const interviewQuestion = activeTab === 'interview' && !finished && operation !== 'end' &&
    operation !== 'restart' && !generationProgress ? state?.question : undefined
  const interviewQuestionVersion = interviewQuestion ? state?.question_version : undefined
  const speechScope = studying ? currentLearningConcept?.id : interviewQuestionVersion
  const savedDeviceId = settings?.tts_device_id ?? null
  const savedVolume = settings?.tts_volume ?? 1
  const output = outputDraft ?? { deviceId: savedDeviceId, volume: savedVolume }
  const missingDevice = output.deviceId !== null && outputDevices !== undefined &&
    !outputDevices.some(({ id }) => id === output.deviceId)
  const { reading, read, stop, isActive: isReading } = useSpeechReading(bridge, speechScope, pendingSpeechStart, savedDeviceId, savedVolume)
  const speakerTest = useSpeechReading(bridge, soundSettingsOpen ? 'speaker-test' : undefined,
    pendingSpeechStart, output.deviceId, output.volume)
  const speakerTestStatus = speakerTest.reading?.status
  const speakerTestActive = speakerTestStatus === 'playing' || speakerTestStatus === 'synthesizing'
  const autoRead = settings?.interview_auto_read !== false
  const interviewReadingStatus = interviewQuestionVersion !== undefined && reading?.itemId === interviewQuestionVersion
    ? reading.status : undefined
  const readingBusy = interviewReadingStatus === 'synthesizing' || interviewReadingStatus === 'playing'
  const voiceQuestion = interviewStarted && !operation && !state?.busy && !syncFailed && !reloadPending && !settingsOpen &&
    (state?.status === 'ready' || state?.status === 'active') ? interviewQuestionVersion : undefined
  const voice = useVoiceAnswer(bridge, voiceQuestion, (text) => {
    const current = answerDraft.current
    updateAnswerDraft(current.trim() ? `${current}\n${text}` : text)
  })
  const canRecord = voiceQuestion !== undefined && !!(settings || settingsReadError) && !readingBusy && !speakerTestActive &&
    (!autoRead || interviewReadingStatus === 'completed' || interviewReadingStatus === 'stopped' || interviewReadingStatus === 'failed')

  useEffect(() => {
    if (interviewQuestionVersion === undefined) {
      lastAutoRead.current = undefined
      return
    }
    if ((!settings && !settingsReadError) || !autoRead || !interviewStarted || !interviewQuestion ||
        lastAutoRead.current === interviewQuestionVersion) return
    lastAutoRead.current = interviewQuestionVersion
    void read(interviewQuestionVersion, interviewQuestion)
  }, [autoRead, interviewQuestion, interviewQuestionVersion, interviewStarted, read, settings, settingsReadError])

  function toggleBranch(key: string) {
    setOpenBranches((current) => {
      const next = new Set(current)
      if (!next.delete(key)) next.add(key)
      return next
    })
  }

  async function toggleScope(ids: string[]) {
    if (!bridge || !state || syncFailed || reloadRequest.current?.active || scopeLocked || (pending.current && pending.current !== 'scope')) return

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
    voice.cancel()
    const id = ++requestId.current
    setOperation('scope')
    setBridgeError('')
    const previousScope = appliedScope.current
    // Serialize bridge calls because each call mutates the session's question.
    let appliedState: InterviewState | undefined
    let appliedModeAtRequestStart: typeof savedEvaluationMode.current
    let needsSync = false
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
          needsSync = true
          syncDraftOrigin.current = state
          setBridgeError('출제 범위 변경 결과를 받지 못했습니다. 현재 선택된 범위를 확인해 주세요.')
          await syncInterview(id)
          break
        }
      } while (target !== requestedScope.current)
    } finally {
      if (appliedState && !needsSync) {
        acceptInterviewState(appliedState, appliedModeAtRequestStart)
        if (!sameScope(previousScope, appliedScope.current) ||
            state.question !== appliedState.question || state.concept_id !== appliedState.concept_id) {
          clearAnswerDraft()
        }
      }
      pending.current = undefined
      setOperation(undefined)
    }
  }

  function drawLearningConcept() {
    if (!learningCandidates.length) return
    const pool = learningCandidates.length > 1
      ? learningCandidates.filter(({ id }) => id !== currentLearningConcept?.id)
      : learningCandidates
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
          {activeTab !== 'authoring' && <button type="button" className="icon-button sidebar-toggle" onClick={() => setSidebarOpen(!sidebarOpen)} aria-controls="topic-sidebar" aria-expanded={sidebarOpen} aria-label="주제 메뉴">
            <Icon name="menu" />
          </button>}
          <span className="brand-mark"><Icon name="message" /></span>
          <span className="brand-name">CS 기술 면접</span>
        </div>
        <nav className="mode-switch" aria-label="앱 화면">
          <button type="button" aria-pressed={activeTab === 'interview'} onClick={() => setActiveTab('interview')}>면접 모드</button>
          <button type="button" aria-pressed={activeTab === 'learn'} onClick={() => { voice.cancel(); setActiveTab('learn') }}>공부 모드</button>
        </nav>
      </header>

      <dialog ref={settingsDialog} aria-labelledby="settings-title" className="settings-dialog" onClose={() => {
        if (settingsDialog.current?.open) return
        speakerTest.stop()
        setSettingsOpen(false)
      }} onClick={(event) => {
        if (event.target === event.currentTarget) event.currentTarget.close()
      }}>
        <section className="settings-content space-y-5">
          <div className="mb-6 flex items-center justify-between gap-3">
            <h2 id="settings-title" className="text-lg font-semibold">설정</h2>
            <button type="button" className="icon-button" autoFocus onClick={() => settingsDialog.current?.close()} aria-label="설정 닫기"><Icon name="x" /></button>
          </div>
          <div className="settings-tabs" role="tablist" aria-label="설정 종류">
            {(['sound', 'api'] as const).map((tab) => (
              <button type="button" key={tab} id={`settings-tab-${tab}`} role="tab"
                aria-controls={`settings-panel-${tab}`} aria-selected={settingsTab === tab}
                tabIndex={settingsTab === tab ? 0 : -1} onClick={() => setSettingsTab(tab)}
                onKeyDown={(event) => {
                  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
                  event.preventDefault()
                  const next = event.key === 'Home' ? 'sound' : event.key === 'End' ? 'api' : tab === 'sound' ? 'api' : 'sound'
                  setSettingsTab(next)
                  document.getElementById(`settings-tab-${next}`)?.focus()
                }}>
                {tab === 'sound' ? '소리 설정' : 'API 설정'}
              </button>
            ))}
          </div>
          {settings && providerSettings ? <>
          <div id="settings-panel-sound" role="tabpanel" aria-labelledby="settings-tab-sound"
            hidden={settingsTab !== 'sound'} className="space-y-5">
            {soundSettingsOpen && <MicrophoneSettings bridge={bridge} savedDeviceId={settings.mic_device_id ?? null}
              savingDisabled={settingsBusy || reloadPending} applySettings={applySettings} />}
            <fieldset className="space-y-2">
              <legend className="mb-2 text-sm font-medium text-slate-700">음성 출력</legend>
              <div className="device-picker">
              <label className="block min-w-0 text-sm">
                스피커·헤드폰
                <select className="settings-input" value={output.deviceId ?? ''} onChange={(event) =>
                  setOutputDraft({ ...output, deviceId: event.target.value || null })}>
                  <option value="">Windows 기본 장치</option>
                  {output.deviceId !== null && !outputDevices?.some(({ id }) => id === output.deviceId) &&
                    <option value={output.deviceId} disabled>{output.deviceId}{missingDevice ? ' (사용할 수 없음)' : ''}</option>}
                  {outputDevices?.map(({ id, name }) => <option key={id} value={id}>{name}</option>)}
                </select>
              </label>
              <button type="button" className="interview-button" onClick={() => setOutputDevicesAttempt((attempt) => attempt + 1)}>
                장치 목록 새로고침
              </button>
              </div>
              {outputDevicesError ? <p role="alert" className="text-sm text-rose-800">출력 장치 목록을 불러오지 못했습니다.</p>
                : missingDevice && <p role="alert" className="text-sm text-rose-800">선택한 장치를 사용할 수 없습니다. Windows 기본 장치 또는 다른 장치를 선택해 주세요.</p>}
              <label className="block text-sm">
                TTS 음량 {Math.round(output.volume * 100)}%
                <input className="block w-full" type="range" min="0" max="1" step="0.01" value={output.volume}
                  onChange={(event) => setOutputDraft({ ...output, volume: event.target.valueAsNumber })} />
              </label>
              <div className="flex flex-wrap gap-2">
                <button type="button" className="interview-button" disabled={!bridge?.tts_test || missingDevice} onClick={() => {
                  if (reading?.status === 'synthesizing' || reading?.status === 'playing') stop()
                  void speakerTest.read('speaker-test')
                }}>스피커 테스트</button>
                {speakerTestActive && <button type="button" className="interview-button" onClick={speakerTest.stop}>테스트 중지</button>}
                <button type="button" className="interview-button ml-auto"
                  disabled={!bridge?.save_tts_output || !outputDraft || settingsBusy || reloadPending || missingDevice}
                  onClick={async () => {
                    if (!bridge?.save_tts_output) return
                    if (await applySettings(() => bridge.save_tts_output!(output.deviceId, output.volume))) {
                      setOutputDraft((current) => current === outputDraft ? undefined : current)
                    }
                  }}>음성 출력 저장</button>
              </div>
              {speakerTestStatus && <p role={speakerTestStatus === 'failed' ? 'alert' : 'status'}
                className={speakerTestStatus === 'failed' ? 'text-sm text-rose-800' : 'sr-only'}>
                {speakerTestActive ? '테스트음을 재생 중입니다.'
                  : speakerTestStatus === 'completed' ? '스피커 테스트를 완료했습니다.'
                  : speakerTestStatus === 'stopped' ? '스피커 테스트를 중지했습니다.'
                  : '테스트음을 재생하지 못했습니다.'}
              </p>}
            </fieldset>
            <label className="flex items-center gap-2 border-t border-slate-200 pt-4 text-sm">
              <input type="checkbox" checked={autoRead} disabled={settingsBusy || reloadPending || !bridge?.save_interview_auto_read}
                onChange={(event) => {
                  const enabled = event.target.checked
                  if (bridge?.save_interview_auto_read) void applySettings(() => bridge.save_interview_auto_read!(enabled))
                }} />
              면접 질문 자동 읽기
            </label>
          </div>
          <div id="settings-panel-api" role="tabpanel" aria-labelledby="settings-tab-api"
            hidden={settingsTab !== 'api'} className="space-y-5">
            <fieldset>
              <legend className="mb-2 text-sm font-medium text-slate-700">다음 면접 제공자</legend>
              <div className="grid grid-cols-2 gap-2">
                {(['gemini', 'openai'] as const).map((provider) => (
                  <label className="provider-card" key={provider}>
                    <input type="radio" name="provider" value={provider} checked={providerSettings.provider === provider} onChange={() => setSettingsDraft({ provider, models: providerSettings.models })} />
                    <span className="block text-sm font-semibold">{provider === 'gemini' ? 'Gemini' : 'Codex'}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="grid gap-3">
              {(['gemini', 'openai'] as const).map((provider) => <section key={provider} className="space-y-3 border-t border-slate-200 pt-4">
                <label className="block text-sm font-medium">
                  {provider === 'gemini' ? 'Gemini 모델' : 'OpenAI 모델'}
                  <input className="settings-input" value={providerSettings.models[provider]} maxLength={120} onChange={(event) => setSettingsDraft({ provider: providerSettings.provider, models: { ...providerSettings.models, [provider]: event.target.value } })} />
                </label>
                <div role="group" aria-labelledby={`${provider}-key-title`}>
                <div className="flex items-baseline justify-between gap-2">
                  <h3 id={`${provider}-key-title`} className="font-semibold">{provider === 'gemini' ? 'Gemini API 키' : 'OpenAI API 키'}</h3>
                  <span className="text-sm text-slate-600">{settings.key_registered[provider] ? '등록됨' : '미등록'}</span>
                </div>
                <label className="mt-3 block text-sm font-medium" htmlFor={`${provider}-key`}>새 키</label>
                <input id={`${provider}-key`} type="password" autoComplete="new-password" value={keyDrafts[provider]} onChange={(event) => setKeyDrafts({ ...keyDrafts, [provider]: event.target.value })} className="settings-input" placeholder="저장 후 다시 표시되지 않습니다." />
                <div className="mt-3 flex flex-wrap gap-2">
                  <button type="button" className="interview-button primary" disabled={reloadPending || settingsBusy || !keyDrafts[provider].trim()} onClick={() => void saveKey(provider)}>{settings.key_registered[provider] ? '키 교체' : '키 등록'}</button>
                  <button type="button" className="interview-button" disabled={reloadPending || settingsBusy || !settings.key_registered[provider]} onClick={() => void applySettings(() => bridge!.delete_api_key(provider))}>키 삭제</button>
                </div>
                </div>
              </section>)}
            </div>
            <button type="button" className="settings-save" disabled={reloadPending || settingsBusy} onClick={() => void saveProviderSettings()}>제공자·모델 저장</button>
          </div>
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
      {activeTab !== 'authoring' && <aside id="topic-sidebar" className="topic-sidebar" data-open={sidebarOpen} aria-label={sidebarLabel}>
        <div className="sidebar-heading">
          <h2 className="text-sm font-semibold">{sidebarLabel}</h2>
          {!studying && <span className="scope-count">{selected.size}개 선택</span>}
          <button type="button" className="icon-button sidebar-toggle" onClick={() => setSidebarOpen(false)} aria-label="주제 메뉴 닫기"><Icon name="x" /></button>
        </div>
        <div className="topic-scroll">
          {!library && (libraryError
            ? <p role="alert">학습 자료를 불러오지 못했습니다.</p>
            : <p role="status">학습 자료를 불러오고 있습니다.</p>)}
          {library && !library.topics.length && <p role="status">사용할 수 있는 지식 데이터가 없습니다. 지식 폴더에 유효한 JSON 파일을 추가한 뒤 앱을 다시 시작해 주세요.</p>}
          {library && library.errors.length > 0 && <div role="alert" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-950">
            <p className="font-semibold">읽지 못한 지식 파일</p>
            <ul className="mt-2 list-disc space-y-1 pl-5">{library.errors.map((error, index) => <li key={`${error.filename}-${index}`}>{error.filename && <><strong>{error.filename}</strong>{': '}</>}{error.detail}</li>)}</ul>
          </div>}
          {!!sidebarTopics?.length && <div>
            {sidebarTopics.map(({ topic, units, ids: topicIds }) => {
              const topicCount = selectedCounts?.get(topic.id) ?? 0
              const topicKey = `topic:${topic.id}`
              const topicOpen = openBranches.has(topicKey)
              return <section className="mb-1" key={topic.id}>
                <div className="tree-row">
                  {!studying && <input aria-label={`${topic.name} 전체 출제 범위`} type="checkbox" checked={topicCount === topicIds.length} ref={(element) => { if (element) element.indeterminate = topicCount > 0 && topicCount < topicIds.length }} disabled={scopeDisabled} onChange={() => void toggleScope(topicIds)} />}
                  <button className="tree-branch" aria-expanded={topicOpen} onClick={() => toggleBranch(topicKey)}><Icon name="chevron" />{topic.name}</button>
                </div>
                {topicOpen && units.map(({ unit, ids, key: unitKey, concepts }) => {
                  const count = selectedCounts?.get(unitKey) ?? 0
                  const unitOpen = openBranches.has(unitKey)
                  return <div className="tree-children" key={unitKey}>
                    <div className="tree-row">{!studying && <input aria-label={`${unit.name} 전체 출제 범위`} type="checkbox" checked={count === ids.length} ref={(element) => { if (element) element.indeterminate = count > 0 && count < ids.length }} disabled={scopeDisabled} onChange={() => void toggleScope(ids)} />}
                      <button className="tree-branch" aria-expanded={unitOpen} onClick={() => toggleBranch(unitKey)}><Icon name="chevron" />{unit.name}</button></div>
                    {unitOpen && concepts.map(({ id, concept }) => {
                      return <div className="tree-row tree-leaf" key={id}>
                        {studying ? (
                          <button type="button" onClick={() => { setLearningConcept(id); setSidebarOpen(false) }} aria-pressed={currentLearningConcept?.id === id}>{concept.name}<span className="tree-question-count" aria-label={`질문 ${concept.questions.length}개`}>{concept.questions.length}</span></button>
                        ) : (
                          <label className="flex min-w-0 items-center gap-2 text-[13px] leading-5 text-slate-600">
                            <input aria-label={`${concept.name} 출제 범위`} type="checkbox" checked={selected.has(id)} disabled={scopeDisabled} onChange={() => void toggleScope([id])} />
                            {concept.name}
                          </label>
                        )}
                      </div>
                    })}
                  </div>
                })}
              </section>
            })}
          </div>}
          {!studying && scopeLocked && <p className="text-sm text-slate-600">면접이 시작되어 출제 범위가 고정되었습니다.</p>}
          {!studying && state && library && library.topics.length > 0 && !selected.size && <p role="status">출제 범위가 비어 있습니다. 면접 질문을 선택할 수 없습니다.</p>}
          {studying ? (
            <div className="flex justify-center">
              <button type="button" className="interview-button primary" disabled={!learningCandidates.length} onClick={drawLearningConcept}>개념 뽑기</button>
            </div>
          ) : state?.status === 'ready' && state.question && (
            <div className="flex justify-center">
              <button type="button" className="interview-button primary" disabled={reloadPending || syncFailed || !!operation} onClick={() => { setSidebarOpen(false); void run('redraw', () => bridge!.redraw_question()) }}>질문 뽑기</button>
            </div>
          )}
        </div>
        <div className="sidebar-footer">
          <button type="button" className="settings-trigger" onClick={() => { voice.cancel(); setSidebarOpen(false); setActiveTab('authoring') }}><Icon name="file-plus" /> 데이터 생성</button>
          <button type="button" className="settings-trigger" onClick={() => {
            voice.cancel()
            settingsDialog.current?.showModal()
            setSettingsOpen(true)
          }}><Icon name="gear" /> 설정</button>
        </div>
      </aside>}

      <main className={`workspace${activeTab === 'authoring' ? ' authoring-workspace' : ''}`} aria-label={activeTab === 'authoring' ? '데이터 제작' : activeTab === 'learn' ? '공부 모드' : '면접 모드'}>
        <div className="workspace-inner">
          {reloadPending && <p role="status" className="panel panel-body">저장한 자료를 반영하고 있습니다.</p>}
          {reloadFailed && <div className="panel panel-body space-y-3">
            <p role="alert" className="text-rose-800">파일은 저장했지만 학습 자료 갱신을 확인하지 못했습니다.</p>
            <button type="button" className="interview-button" disabled={!bridge || !!operation}
              onClick={() => void reloadKnowledge(true)}>학습 자료 다시 불러오기</button>
          </div>}
          <div hidden={activeTab !== 'authoring'}><KnowledgeAuthoring bridge={bridge} reloading={reloadPending} onSaved={reloadKnowledge} /></div>
          {activeTab === 'learn' && (
            <>
              {bridgeError && <p role="alert" className="mb-4 text-rose-800">{bridgeError}</p>}
              {currentLearningConcept ? (
                <div className="space-y-5">
                    {currentLearningConcept.concept.questions.map((question, index, questions) => {
                      const status = reading?.itemId === question.id ? reading.status : undefined
                      return (
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
                            <h3 className="mb-3 text-sm font-semibold text-slate-800">예시 답안</h3>
                            <p className="whitespace-pre-wrap break-words text-base leading-8 text-slate-700">{question.example_answer}</p>
                          </article>
                          <div className="mt-4 flex flex-wrap items-start gap-2">
                            {index === questions.length - 1 && <button type="button" className="interview-button primary" onClick={() => {
                              setLearningConcept((nextLearningConcept ?? currentLearningConcept).id)
                              window.scrollTo(0, 0)
                            }}>다음 개념</button>}
                            <ReadingControls status={status} playLabel="읽기" stopLabel="중지" stop={stop}
                              play={() => void read(question.id, question.prompt, question.example_answer?.trim() ? question.example_answer : undefined)} />
                          </div>
                        </div>
                      </section>
                      )
                    })}
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
              {previousDrafts.length > 0 && <details className="px-6 py-4 text-sm text-slate-700">
                <summary className="cursor-pointer font-medium">이전 답변 초안</summary>
                {previousDrafts.map(({ question, answer }, index) => <div key={index} className="mt-3 space-y-1">
                  <p className="font-medium">{question}</p>
                  <p className="whitespace-pre-wrap wrap-anywhere">{answer}</p>
                </div>)}
              </details>}
              {operation === 'sync' ? <p role="status" className="px-6 py-4 text-slate-600">면접 상태를 확인하고 있습니다.</p>
                : syncFailed && <div className="px-6 py-4 space-y-3">
                  <p role="alert" className="text-rose-800">면접 상태를 확인하지 못했습니다. 상태를 확인한 뒤 면접을 계속할 수 있습니다.</p>
                  <button type="button" className="interview-button" disabled={!bridge || !!operation}
                    onClick={() => void retryInterviewSync()}>면접 상태 다시 확인</button>
                </div>}
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
                  {!selected.size && <p className="mt-2 text-slate-600">새 면접을 시작하려면 면접 주제에서 개념을 선택해 주세요.</p>}
                  <button type="button" className="interview-button primary mt-4" disabled={reloadPending || syncFailed || !bridge || !!operation || !selected.size}
                    onClick={() => { if (bridge) void run('restart', () => bridge.restart_interview()) }}>새 면접</button>
                </div>
              )}
              {state?.status === 'empty' && <p role="status" className="panel-empty">{library && library.topics.length === 0 ? '사용할 수 있는 지식 데이터가 없습니다. 학습 화면에서 지식 파일 오류를 확인하거나 유효한 JSON 파일을 추가해 주세요.' : '출제 범위가 비어 있어 질문을 뽑을 수 없습니다.'}</p>}
              {bridge && state?.concept_id && !finished && (
                <>
                  <div className="question-header">
                    <div className="question-meta">
                      <span className="question-label">{hasFollowUp ? '꼬리 질문' : '면접 질문'}</span>
                      <span>{generationProgress?.concept_name ?? state.concept_name}</span>
                    </div>
                    {!generationProgress && state.question && <h2 className="question-text">{state.question}</h2>}
                    {readingBusy && <p role="status" className="question-reading-status">
                      {interviewReadingStatus === 'playing' ? '읽는 중' : '음성 준비 중'}
                    </p>}
                    {state.status === 'ready' && !state.question && !operation && (
                      <h2 className="question-text">메시지를 보내 면접을 시작하세요</h2>
                    )}
                  </div>
                  <AnswerForm
                    draft={draft}
                    onStart={interviewQuestion && interviewQuestionVersion !== undefined && !interviewStarted
                      ? () => setInterviewStarted(true) : undefined}
                    voiceBusy={!!voice.state}
                    readingControls={interviewQuestion && interviewQuestionVersion !== undefined && interviewStarted && (
                      <ReadingControls status={interviewReadingStatus} playLabel="다시 듣기" stopLabel="읽기 중지"
                        showBusyStatus={false}
                        disabled={!!voice.state}
                        play={() => { if (!voice.isActive()) void read(interviewQuestionVersion, interviewQuestion) }} stop={stop} />
                    )}
                    voiceControls={voice.available && interviewStarted ? (
                      <div className="answer-voice">
                        <div className="flex flex-wrap items-center gap-2">
                          {!voice.state && <button type="button" className="interview-button" disabled={!canRecord}
                            onClick={() => {
                              if (canRecord && !pending.current && !isReading() && !speakerTest.isActive()) {
                                void voice.start(settings?.mic_device_id ?? null)
                              }
                            }}>녹음 시작</button>}
                          {voice.state?.phase === 'recording' && <button type="button" className="interview-button" onClick={voice.stop}>녹음 종료</button>}
                          {voice.state && <button type="button" className="interview-button" aria-label="음성 답변 취소" onClick={() => voice.cancel(true)}>취소</button>}
                        </div>
                        {voice.state && <p role="status" className="text-sm text-slate-600">
                          {voice.state.phase === 'opening' && '마이크를 여는 중입니다.'}
                          {voice.state.phase === 'recording' && `녹음 중 · ${Math.floor(voice.state.elapsedMs / 1000)}초 / 180초`}
                          {voice.state.phase === 'stopping' && '녹음을 종료하고 있습니다.'}
                          {voice.state.phase === 'transcribing' && '음성을 텍스트로 변환하고 있습니다.'}
                        </p>}
                        {voice.notice && <p role={voice.notice.error ? 'alert' : 'status'} className={voice.notice.error ? 'text-sm text-rose-800' : 'text-sm text-slate-600'}>
                          {voice.notice.text}
                        </p>}
                      </div>
                    ) : undefined}
                    onDraftChange={updateAnswerDraft}
                    state={state}
                    operation={operation}
                    hasFollowUp={hasFollowUp}
                    generationProgress={generationProgress}
                    syncFailed={syncFailed || reloadPending}
                    onSubmit={(draft) => {
                      if (voice.isActive()) return
                      if (state.status === 'error') void run('evaluate', bridge.retry_answer)
                      else if (state.status === 'question_error') void run('generate', bridge.retry_answer)
                      else void run(state.question ? 'evaluate' : 'generate', (recordStart) => bridge.submit_answer(draft, state.evaluation_mode, recordStart, state.question_version))
                    }}
                    onRedraw={() => void run('redraw', () => bridge.redraw_question())}
                    onNext={() => {
                      const token = generationProgress?.token
                      if (token === undefined) void run('next', bridge.next_concept)
                      else void run('next', (recordStart) => bridge.next_concept(recordStart, token), token)
                    }}
                    onEnd={() => void run('end', bridge.end_interview)}
                  />
                </>
              )}
            </section>

            {activeTab === 'interview' && <aside className="panel panel-body space-y-4">
              <h2 className="text-lg font-semibold">면접 기록</h2>
              {state?.records.map((record) => (record.skipped || record.turns.length > 0 || record.unanswered_questions.length > 0) && (
                <InterviewRecord key={record.concept_id} record={record} />
              ))}
            </aside>}
          </div>
          {activeTab === 'interview' && bridge && state?.concept_id && !finished && (
            <InterviewFooter state={state} operation={operation} generationProgress={generationProgress} />
          )}
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
    invalid_settings: '입력한 설정 값을 확인해 주세요.',
    invalid_key: '저장할 키를 입력해 주세요.',
    invalid_provider: '지원하지 않는 제공자입니다.',
    write_failed: '파일을 저장하지 못했습니다. 기존 저장 데이터는 유지되었습니다.',
    folder_open_failed: '지식 폴더를 열지 못했습니다.',
  }
  return messages[code]
}
