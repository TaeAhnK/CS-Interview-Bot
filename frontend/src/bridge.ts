export type Evaluation = {
  correct_points: string[]
  missing_points: string[]
  incorrect_points: string[]
}

export type ConceptRecord = {
  concept_id: string
  concept_name: string
  unanswered_questions: string[]
  turns: { question: string; answer: string; evaluation?: Evaluation }[]
}

export type InterviewState = {
  evaluation_mode: 'fake' | 'gemini'
  submission_blocked?: string
  status: 'ready' | 'empty' | 'active' | 'evaluating' | 'error' | 'finished'
  question: string | null
  concept_id: string | null
  concept_name: string | null
  busy: boolean
  error: string | null
  records: ConceptRecord[]
}

export type InterviewBridge = {
  learning_data: () => Promise<KnowledgeLibraryData>
  set_interview_scope: (conceptIds: string[]) => Promise<InterviewState>
  redraw_question: () => Promise<InterviewState>
  submit_answer: (answer: string, expectedMode: InterviewState['evaluation_mode'], recordStart?: number) => Promise<InterviewState>
  retry_answer: (recordStart?: number) => Promise<InterviewState>
  next_concept: (recordStart?: number) => Promise<InterviewState>
  end_interview: (recordStart?: number) => Promise<InterviewState>
  settings_state: () => Promise<SettingsResult>
  save_provider_settings: (provider: ProviderId, models: Record<ProviderId, string>) => Promise<SettingsResult>
  save_api_key: (provider: ProviderId, key: string) => Promise<SettingsResult>
  delete_api_key: (provider: ProviderId) => Promise<SettingsResult>
  open_knowledge_folder: () => Promise<{ ok: boolean; error?: SettingsErrorCode }>
}

export type KnowledgeQuestion = { id: string; prompt: string; example_answer: string }
export type KnowledgeConcept = { id: string; name: string; questions: KnowledgeQuestion[] }
export type KnowledgeUnit = { id: string; name: string; concepts: KnowledgeConcept[] }
export type KnowledgeTopic = { schema_version: string; topic: { id: string; name: string; units: KnowledgeUnit[] } }
export type KnowledgeLibraryData = {
  topics: KnowledgeTopic[]
  errors: { filename: string; detail: string }[]
}

export type ProviderId = 'gemini' | 'openai'
export type SettingsErrorCode =
  | 'dpapi_unavailable' | 'decrypt_failed' | 'encrypt_failed' | 'key_file_unavailable'
  | 'settings_unavailable' | 'invalid_settings' | 'invalid_key' | 'invalid_provider'
  | 'write_failed' | 'folder_open_failed'
export type SettingsData = {
  provider: ProviderId
  models: Record<ProviderId, string>
  key_registered: Record<ProviderId, boolean>
}
export type SettingsResult =
  | { ok: true; settings: SettingsData; evaluation_mode: InterviewState['evaluation_mode'] }
  | { ok: false; error: SettingsErrorCode }
