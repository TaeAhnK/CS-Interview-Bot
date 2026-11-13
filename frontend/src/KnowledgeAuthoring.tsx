import { memo, useCallback, useState } from 'react'
import { flushSync } from 'react-dom'
import type { KnowledgeAuthoringBridge, KnowledgeConcept, KnowledgeFile, KnowledgeFileError, KnowledgeQuestion, KnowledgeTopic, KnowledgeUnit } from './bridge'

const fileErrors: Record<KnowledgeFileError['error'], string> = {
  duplicate_topic: '같은 주제가 이미 있습니다. 기존 파일을 열어 편집할 수 있습니다.',
  duplicate_check_failed: '파일을 읽을 수 없어 주제 중복 검사를 완료하지 못했습니다.',
  confirmation_required: '기존 파일을 덮어쓸지 확인해 주세요.',
  file_changed: '파일이 열린 뒤 디스크에서 변경되었습니다. 수정 내용을 확인한 뒤 파일을 다시 열어 주세요.',
  topic_id_changed: '기존 주제의 ID가 변경되어 저장할 수 없습니다.',
  invalid_document: '저장 형식에 맞지 않는 항목이 있습니다.',
  file_not_found: '지식 파일을 찾을 수 없습니다.',
  read_failed: '지식 파일을 읽지 못했습니다.',
  save_failed: '지식 파일을 저장하지 못했습니다.',
  file_too_large: '지식 파일은 최대 2 MiB까지 열거나 저장할 수 있습니다.',
  library_too_large: '저장하면 학습 자료 전체 한도인 32 MiB를 초과합니다.',
  library_file_limit: '새로 저장하면 학습 자료 주제 파일 한도인 100개를 초과합니다.',
}

const KnowledgePreview = memo(function KnowledgePreview({ document }: { document: KnowledgeTopic }) {
  const { topic } = document
  return <details open className="authoring-preview">
    <summary className="tree-preview-branch">{topic.name}</summary>
    {topic.units.map((unit) => <details open className="authoring-preview-unit" key={unit.id}>
      <summary className="tree-preview-branch">{unit.name}</summary>
      <ul>
        {unit.concepts.map((concept) => <li className="authoring-preview-concept" key={concept.id}>
          <span className="min-w-0 text-sm leading-5 text-slate-700">{concept.name}</span>
          <span className="authoring-preview-counts">
            <span>질문 {concept.questions.length}개</span>
            <span>예시 답안 {concept.questions.filter((question) => question.example_answer?.trim()).length}개</span>
          </span>
        </li>)}
      </ul>
    </details>)}
  </details>
})

type Update<T> = (current: T) => T
type EditItem<T> = (index: number, update: Update<T>) => void
type Topic = KnowledgeTopic['topic']

function updateItem<T>(items: T[], index: number, update: Update<T>) {
  const next = items.slice()
  next[index] = update(items[index])
  return next
}

function newConcept(): KnowledgeConcept {
  return { id: crypto.randomUUID(), name: '', questions: [] }
}

const KnowledgeTree = memo(function KnowledgeTree({ document, onChange }: {
  document: KnowledgeTopic
  onChange: (update: Update<Topic>) => void
}) {
  const { topic } = document
  const editUnit: EditItem<KnowledgeUnit> = useCallback((index, update) => {
    onChange((current) => ({ ...current, units: updateItem(current.units, index, update) }))
  }, [onChange])
  const removeUnit = useCallback((index: number) => {
    onChange((current) => ({ ...current, units: current.units.filter((_, i) => i !== index) }))
  }, [onChange])

  return <div className="space-y-4">
    <label className="settings-field">미리보기 주제명
      <input className="settings-input" value={topic.name} onChange={(event) => {
        const name = event.target.value
        onChange((current) => ({ ...current, name }))
      }} />
    </label>
    {topic.units.map((unit, index) => <UnitEditor key={unit.id} unit={unit} index={index}
      onEdit={editUnit} onRemove={topic.units.length > 1 ? removeUnit : undefined} />)}
    <button type="button" className="interview-button" onClick={() => {
      const unit = { id: crypto.randomUUID(), name: '', concepts: [newConcept()] }
      onChange((current) => ({ ...current, units: [...current.units, unit] }))
    }}>단원 추가</button>
  </div>
})

const UnitEditor = memo(function UnitEditor({ unit, index, onEdit, onRemove }: {
  unit: KnowledgeUnit
  index: number
  onEdit: EditItem<KnowledgeUnit>
  onRemove?: (index: number) => void
}) {
  const editConcept: EditItem<KnowledgeConcept> = useCallback((conceptIndex, update) => {
    onEdit(index, (current) => ({ ...current, concepts: updateItem(current.concepts, conceptIndex, update) }))
  }, [index, onEdit])
  const removeConcept = useCallback((conceptIndex: number) => {
    onEdit(index, (current) => ({ ...current, concepts: current.concepts.filter((_, i) => i !== conceptIndex) }))
  }, [index, onEdit])

  return <fieldset className="space-y-3 rounded-xl border border-slate-200 p-4">
    <legend className="px-1 text-sm font-semibold">단원 {index + 1}</legend>
    <label className="settings-field">단원명
      <input className="settings-input" value={unit.name} onChange={(event) => {
        const name = event.target.value
        onEdit(index, (current) => ({ ...current, name }))
      }} />
    </label>
    {unit.concepts.map((concept, conceptIndex) => <ConceptEditor key={concept.id} concept={concept} index={conceptIndex}
      onEdit={editConcept} onRemove={unit.concepts.length > 1 ? removeConcept : undefined} />)}
    <div className="flex flex-wrap gap-2">
      <button type="button" className="interview-button" onClick={() => {
        const concept = newConcept()
        onEdit(index, (current) => ({ ...current, concepts: [...current.concepts, concept] }))
      }}>개념 추가</button>
      {onRemove && <button type="button" className="interview-button" onClick={() => onRemove(index)}>단원 삭제</button>}
    </div>
  </fieldset>
})

const ConceptEditor = memo(function ConceptEditor({ concept, index, onEdit, onRemove }: {
  concept: KnowledgeConcept
  index: number
  onEdit: EditItem<KnowledgeConcept>
  onRemove?: (index: number) => void
}) {
  function editQuestion(questionIndex: number, fields: Partial<Pick<KnowledgeQuestion, 'prompt' | 'example_answer'>>) {
    onEdit(index, (current) => ({
      ...current,
      questions: updateItem(current.questions, questionIndex, (question) => ({ ...question, ...fields })),
    }))
  }

  return <fieldset className="space-y-3 border-l-2 border-slate-200 pl-4">
    <legend className="text-sm font-semibold">개념 {index + 1}</legend>
    <label className="settings-field">개념명
      <input className="settings-input" value={concept.name} onChange={(event) => {
        const name = event.target.value
        onEdit(index, (current) => ({ ...current, name }))
      }} />
    </label>
    {concept.questions.map((question, q) => <fieldset key={question.id} className="space-y-3 rounded-lg bg-slate-50 p-3">
      <legend className="text-sm font-semibold">질문 {q + 1}</legend>
      <label className="settings-field">질문 내용
        <input className="settings-input" value={question.prompt} onChange={(event) => editQuestion(q, { prompt: event.target.value })} />
      </label>
      <label className="settings-field">예시 답안 (선택)
        <textarea className="settings-input" rows={3} value={question.example_answer ?? ''}
          onChange={(event) => editQuestion(q, { example_answer: event.target.value || null })} />
      </label>
      <button type="button" className="interview-button" onClick={() => onEdit(index, (current) => ({
        ...current, questions: current.questions.filter((_, i) => i !== q),
      }))}>질문 삭제</button>
    </fieldset>)}
    {!concept.questions.length && <p className="text-sm text-slate-500">등록된 질문이 없습니다.</p>}
    <div className="flex flex-wrap gap-2">
      <button type="button" className="interview-button" disabled={concept.questions.length >= 2} onClick={() => {
        const question = { id: crypto.randomUUID(), prompt: '' }
        onEdit(index, (current) => ({ ...current, questions: [...current.questions, question] }))
      }}>질문 추가</button>
      {onRemove && <button type="button" className="interview-button" onClick={() => onRemove(index)}>개념 삭제</button>}
    </div>
  </fieldset>
})

type AuthoringTree = {
  document: KnowledgeTopic
  dirty: boolean
} & (
  | { kind: 'draft'; input: { topicName: string; body: string } }
  | { kind: 'file'; source: Pick<KnowledgeFile, 'filename' | 'revision'> }
)

export default memo(function KnowledgeAuthoring({ bridge, reloading, onSaved }: {
  bridge?: KnowledgeAuthoringBridge
  reloading: boolean
  onSaved: () => Promise<void>
}) {
  const [topicName, setTopicName] = useState('')
  const [body, setBody] = useState('')
  const [files, setFiles] = useState<string[]>()
  const [filename, setFilename] = useState('')
  const [tree, setTree] = useState<AuthoringTree>()
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | KnowledgeFileError>()
  const [saved, setSaved] = useState(false)
  const fileError = typeof error === 'object' ? error : undefined
  const duplicateFilename = fileError?.error === 'duplicate_topic' ? fileError.filename : undefined
  const document = tree?.document
  const opened = tree?.kind === 'file' ? tree.source : undefined
  const previewOutdated = tree?.kind === 'draft' && (tree.input.topicName !== topicName || tree.input.body !== body)

  const editDocument = useCallback((update: Update<Topic>) => {
    setTree((current) => current && {
      ...current, dirty: true, document: { ...current.document, topic: update(current.document.topic) },
    })
    setSaved(false)
  }, [])

  function canDiscardTree() {
    return !tree?.dirty || window.confirm('저장하지 않은 트리 변경 사항을 버릴까요?')
  }

  async function run(operation: (api: KnowledgeAuthoringBridge) => Promise<void>, failure: string) {
    if (!bridge || busy) return
    setBusy(true)
    setError(undefined)
    setSaved(false)
    try {
      await operation(bridge)
    } catch {
      setError(failure)
    } finally {
      setBusy(false)
    }
  }

  function refreshFiles() {
    return run(async (api) => {
      const result = await api.list_knowledge_files()
      if (result.ok) {
        setFiles(result.files)
        setFilename((current) => result.files.includes(current) ? current : '')
      } else setError(result)
    }, '파일 목록을 불러오지 못했습니다.')
  }

  function openFile(file: string) {
    return run(async (api) => {
      const result = await api.open_knowledge_file(file)
      if (result.ok) {
        if (!canDiscardTree()) return
        setTree({ kind: 'file', document: result.document, source: { filename: result.filename, revision: result.revision }, dirty: false })
        setEditing(false)
      } else setError(result)
    }, '지식 파일을 열지 못했습니다.')
  }

  function clearFeedback() {
    if (error) setError(undefined)
    if (saved) setSaved(false)
  }

  function startDraft() {
    if (!canDiscardTree()) return
    setTree(undefined)
    setEditing(false)
    clearFeedback()
  }

  function preview() {
    return run(async (api) => {
      const result = await api.parse_knowledge_text(topicName, body)
      if (result.ok) {
        if (!canDiscardTree()) return
        setTree({ kind: 'draft', document: result.document, input: { topicName, body }, dirty: false })
      } else setError(`${result.error.line}줄: ${result.error.message}`)
    }, '미리보기를 만들지 못했습니다.')
  }

  function save() {
    if (!document || previewOutdated || reloading) return
    setConfirming(false)
    return run(async (api) => {
      const result = opened
        ? await api.save_knowledge_file(document, opened.filename, opened.revision, true)
        : await api.save_knowledge_file(document)
      if (result.ok) {
        setTree({ kind: 'file', document: result.document, source: { filename: result.filename, revision: result.revision }, dirty: false })
        setSaved(true)
        setFiles((current) => !current || current.includes(result.filename) ? current : [...current, result.filename].sort())
        await onSaved()
      } else setError(result)
    }, '지식 파일을 저장하지 못했습니다.')
  }

  return <section className="panel authoring-panel space-y-5" aria-labelledby="authoring-title" aria-busy={busy}>
    <h1 id="authoring-title" className="text-xl font-semibold">데이터 제작</h1>
    {!bridge && <p role="status" className="text-sm text-slate-600">앱에 연결되면 지식 데이터를 편집할 수 있습니다.</p>}
    <fieldset disabled={!bridge || busy || reloading || confirming} className="min-w-0 space-y-5">
      <section className="space-y-3" aria-labelledby="authoring-files-title">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="authoring-files-title" className="text-base font-medium">저장된 파일</h2>
          <button type="button" className="interview-button" onClick={() => void refreshFiles()}>{files ? '목록 새로고침' : '기존 파일 목록'}</button>
        </div>
        {!!files?.length && <div className="authoring-file-picker">
          <label className="settings-field min-w-0">기존 지식 파일
            <select className="settings-input" value={filename} onChange={(event) => setFilename(event.target.value)}>
              <option value="">파일 선택</option>
              {files.map((file) => <option key={file}>{file}</option>)}
            </select>
          </label>
          <button type="button" className="interview-button justify-self-start" disabled={!filename} onClick={() => void openFile(filename)}>파일 열기</button>
        </div>}
        {files?.length === 0 && <p className="text-sm text-slate-600">저장된 지식 파일이 없습니다.</p>}
      </section>
      {opened ? <div className="flex flex-wrap items-center justify-between gap-2 border-t border-slate-200 pt-5">
        <p className="min-w-0 break-all text-sm text-slate-600">편집 중인 파일: {opened.filename}</p>
        <button type="button" className="interview-button" onClick={startDraft}>새 주제 작성</button>
      </div> : <section className="space-y-4 border-t border-slate-200 pt-5" aria-labelledby="authoring-draft-title">
        <h2 id="authoring-draft-title" className="text-base font-medium">새 주제 작성</h2>
        <label className="settings-field">주제명
          <input className="settings-input" value={topicName} onChange={(event) => {
            const value = event.target.value
            if (value !== topicName) { setTopicName(value); clearFeedback() }
          }} />
        </label>
        <label className="settings-field">본문
          <textarea className="settings-input authoring-outline" rows={8} value={body} spellCheck={false} aria-describedby="authoring-outline-help"
            placeholder={'단원\n- 개념\n  - (질문)\n    - (예시 답변)'}
            onKeyDown={(event) => {
              if (event.key !== 'Tab' || event.shiftKey || event.altKey || event.ctrlKey || event.metaKey || event.nativeEvent.isComposing) return
              event.preventDefault()
              const field = event.currentTarget
              const { selectionStart, selectionEnd, value } = field
              const next = value.slice(0, selectionStart) + '  ' + value.slice(selectionEnd)
              if (next !== value) {
                flushSync(() => {
                  setBody(next)
                  clearFeedback()
                })
              }
              if (field.ownerDocument.activeElement === field) {
                field.setSelectionRange(selectionStart + 2, selectionStart + 2)
              }
            }}
            onChange={(event) => {
              const value = event.target.value
              if (value !== body) { setBody(value); clearFeedback() }
            }} />
        </label>
        <div id="authoring-outline-help" className="space-y-2 text-sm leading-5 text-slate-600">
          <p>단원은 줄 맨 앞에 적으세요. 개념은 줄 맨 앞이나 한 단계 들여쓰고, 질문과 답안은 각각 한 단계씩 더 들여쓰세요.</p>
          <p><kbd>Tab</kbd> 공백 두 칸 삽입 <span aria-hidden="true" className="mx-2 text-slate-400">·</span><kbd>Shift+Tab</kbd> 이전 입력칸으로 이동</p>
        </div>
        <button type="button" className="interview-button" onClick={() => void preview()}>미리보기 만들기</button>
      </section>}
      {document && <div className="space-y-4 border-t border-slate-200 pt-5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-medium">{editing ? '트리 편집' : '트리 미리보기'}</h2>
          <button type="button" className="interview-button" onClick={() => setEditing(!editing)}>{editing ? '미리보기로 돌아가기' : '트리 편집'}</button>
        </div>
        {previewOutdated && <p id="authoring-outdated-preview" role="status" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
          아래 트리는 이전 원본을 기준으로 합니다. 변경한 원본을 저장하려면 미리보기를 다시 만드세요.
        </p>}
        {editing
          ? <KnowledgeTree document={document} onChange={editDocument} />
          : <KnowledgePreview document={document} />}
        <button type="button" className="interview-button primary" disabled={previewOutdated}
          aria-describedby={previewOutdated ? 'authoring-outdated-preview' : undefined}
          onClick={() => opened ? setConfirming(true) : void save()}>{opened ? '변경 사항 저장' : '새 주제 저장'}</button>
      </div>}
    </fieldset>
    {confirming && <div role="group" aria-label="덮어쓰기 확인" className="space-y-2 rounded-lg bg-slate-100 p-3">
      <p className="break-all text-sm">{opened?.filename} 파일을 덮어쓸까요?</p>
      <div className="flex gap-2">
        <button type="button" className="interview-button" onClick={() => setConfirming(false)}>취소</button>
        <button type="button" className="interview-button primary" onClick={() => void save()}>덮어쓰기</button>
      </div>
    </div>}
    {error && <div className="space-y-2 rounded-lg bg-rose-50 p-3 text-sm text-rose-900">
      <div role="alert">
        <p>{typeof error === 'string' ? error : fileErrors[error.error]}</p>
        {fileError?.error === 'duplicate_check_failed' && fileError.filename && <p className="break-all">읽지 못한 파일: {fileError.filename}</p>}
        {!!fileError?.issues?.length && <ul className="list-disc pl-5">
          {fileError.issues.map(({ path, message }, index) => <li key={index}>{path.join('.') || '문서'}: {message}</li>)}
        </ul>}
        {!!fileError?.omitted_issues && <p>그 외 오류 {fileError.omitted_issues.toLocaleString('ko-KR')}건</p>}
      </div>
      {duplicateFilename && <button type="button" className="interview-button" disabled={busy || confirming}
        onClick={() => void openFile(duplicateFilename)}>중복 파일 열기</button>}
    </div>}
    {saved && <p role="status" className="text-sm text-slate-700">저장했습니다.</p>}
  </section>
})
