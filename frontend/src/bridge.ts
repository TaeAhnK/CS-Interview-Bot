export type Evaluation = {
  correct_points: string[]
  missing_points: string[]
  incorrect_points: string[]
}

export type ConceptRecord = {
  concept_id: string
  concept_name: string
  skipped: boolean
  unanswered_questions: string[]
  turns: { question: string; answer: string; evaluation?: Evaluation }[]
}

export type InterviewState = {
  evaluation_mode: 'fake' | 'gemini'
  submission_blocked?: string
  question_changed?: boolean
  status: 'ready' | 'empty' | 'active' | 'evaluating' | 'error' | 'finished' | 'generating_question' | 'question_error' | 'question_unavailable'
  question_generation_available?: boolean
  question: string | null
  question_version: number
  scope_ids?: string[]
  concept_id: string | null
  concept_name: string | null
  busy: boolean
  error: string | null
  records: ConceptRecord[]
}

export type QuestionGeneration = { token: number; concept_name: string }

export type InterviewSnapshot = InterviewState & { scope_ids: string[] }
export type TtsStatus = {
  request_id: number
  status: 'idle' | 'synthesizing' | 'playing' | 'completed' | 'stopped' | 'failed'
  error: 'invalid_request' | 'model_missing' | 'engine_unavailable' | 'synthesis_failed'
    | 'audio_unavailable' | 'device_unavailable' | 'playback_failed' | null
}

export type TtsBridge = {
  tts_devices: () => Promise<{ ok: true; devices: { id: string; name: string }[] } | { ok: false; error: 'audio_unavailable' }>
  tts_play: (text: string, deviceId?: string | null, volume?: number) => Promise<TtsStatus>
  tts_play_study: (question: string, answer: string, deviceId: string | null, volume: number) => Promise<TtsStatus>
  tts_test: (deviceId: string | null, volume: number) => Promise<TtsStatus>
  tts_status: () => Promise<TtsStatus>
  tts_stop: (requestId?: number) => Promise<TtsStatus>
}

export type MicrophoneError = 'audio_unavailable' | 'device_unavailable' | 'permission_denied'
  | 'capture_failed' | 'empty_recording' | 'invalid_request' | 'busy' | 'closed' | 'stale_request'

export type MicrophoneStatus = { ok: false; error: MicrophoneError } | {
  ok: true
  request_id: number
  status: 'starting' | 'recording' | 'stopping' | 'completed' | 'cancelled' | 'failed'
  error: MicrophoneError | null
  reason: 'manual' | 'limit' | null
  elapsed_ms: number
  level: number
  meter_only: boolean
  has_recording: boolean
}

export type MicrophoneBridge = {
  mic_devices: () => Promise<{ ok: true; devices: { id: string; name: string }[] } | { ok: false; error: MicrophoneError }>
  mic_start: (deviceId?: string | null, meterOnly?: boolean) => Promise<MicrophoneStatus>
  mic_status: (requestId: number) => Promise<MicrophoneStatus>
  mic_stop: (requestId: number) => Promise<MicrophoneStatus>
  mic_cancel: (requestId: number) => Promise<MicrophoneStatus>
}

export type TranscriptionError = 'closed' | 'busy' | 'recording_unavailable' | 'stale_request'
  | 'result_unavailable' | 'invalid_audio' | 'engine_unavailable' | 'model_missing'
  | 'timeout' | 'transcription_failed'

type TranscriptionRejection = { ok: false; error: TranscriptionError }

export type TranscriptionStatus = TranscriptionRejection | {
  ok: true
  request_id: number
  recording_id: number
  status: 'transcribing' | 'completed' | 'empty' | 'failed' | 'cancelled'
  error: TranscriptionError | null
  has_result: boolean
}

export type TranscriptionBridge = {
  stt_start: (recordingId: number) => Promise<TranscriptionStatus>
  stt_status: (requestId: number) => Promise<TranscriptionStatus>
  stt_take_result: (requestId: number) => Promise<TranscriptionRejection | {
    ok: true; request_id: number; recording_id: number; text: string
  }>
  stt_cancel: (requestId: number) => Promise<TranscriptionStatus>
}

export type InterviewBridge = KnowledgeAuthoringBridge & Partial<MicrophoneBridge & TranscriptionBridge> & {
  interview_state: (includeLibrary?: boolean) => Promise<InterviewSnapshot & { library?: KnowledgeLibraryData; reload_id?: string | null }>
  interview_busy: () => Promise<boolean>
  learning_data: () => Promise<KnowledgeLibraryData>
  set_interview_scope: (conceptIds: string[]) => Promise<InterviewState>
  restart_interview: () => Promise<InterviewState>
  redraw_question: () => Promise<InterviewState>
  submit_answer: (answer: string, expectedMode: InterviewState['evaluation_mode'], recordStart: number, expectedQuestionVersion: number) => Promise<InterviewState>
  retry_answer: (recordStart?: number) => Promise<InterviewState>
  question_generation: () => Promise<QuestionGeneration | null>
  next_concept: (recordStart?: number, generationToken?: number) => Promise<InterviewState>
  end_interview: (recordStart?: number) => Promise<InterviewState>
  settings_state: () => Promise<SettingsResult>
  save_provider_settings: (provider: ProviderId, models: Record<ProviderId, string>) => Promise<SettingsResult>
  save_interview_auto_read?: (enabled: boolean) => Promise<SettingsResult>
  save_tts_output?: (deviceId: string | null, volume: number) => Promise<SettingsResult>
  save_mic_input?: (deviceId: string | null) => Promise<SettingsResult>
  save_api_key: (provider: ProviderId, key: string) => Promise<SettingsResult>
  delete_api_key: (provider: ProviderId) => Promise<SettingsResult>
  open_knowledge_folder: () => Promise<{ ok: boolean; error?: SettingsErrorCode }>
}

export type KnowledgeQuestion = { id: string; prompt: string; example_answer?: string | null }
export type KnowledgeConcept = { id: string; name: string; questions: KnowledgeQuestion[] }
export type KnowledgeUnit = { id: string; name: string; concepts: KnowledgeConcept[] }
export type KnowledgeTopic = { schema_version: string; topic: { id: string; name: string; units: KnowledgeUnit[] } }
export type KnowledgeFile = { filename: string; revision: string; document: KnowledgeTopic }
export type KnowledgeFileError = {
  ok: false
  error: 'duplicate_topic' | 'confirmation_required' | 'file_changed' | 'topic_id_changed'
    | 'invalid_document' | 'file_not_found' | 'read_failed' | 'save_failed'
    | 'file_too_large' | 'library_too_large' | 'library_file_limit' | 'duplicate_check_failed'
  filename?: string
  issues?: { path: (string | number)[]; message: string }[]
  omitted_issues?: number
}
export type KnowledgeAuthoringBridge = {
  reload_knowledge: (reloadId: string) => Promise<{ library: KnowledgeLibraryData; interview: InterviewSnapshot }>
  parse_knowledge_text: (topicName: string, body: string) => Promise<
    { ok: true; document: KnowledgeTopic } | { ok: false; error: { line: number; message: string } }
  >
  list_knowledge_files: () => Promise<{ ok: true; files: string[] } | KnowledgeFileError>
  open_knowledge_file: (filename: string) => Promise<({ ok: true } & KnowledgeFile) | KnowledgeFileError>
  save_knowledge_file: (document: KnowledgeTopic, filename?: string, revision?: string, overwrite?: true) => Promise<
    ({ ok: true } & KnowledgeFile) | KnowledgeFileError
  >
}
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
  tts_device_id?: string | null
  tts_volume?: number
  mic_device_id?: string | null
  provider: ProviderId
  models: Record<ProviderId, string>
  key_registered: Record<ProviderId, boolean>
  interview_auto_read?: boolean
}
export type SettingsResult =
  | { ok: true; settings: SettingsData; evaluation_mode: InterviewState['evaluation_mode']; question_generation_available?: boolean }
  | { ok: false; error: SettingsErrorCode }
