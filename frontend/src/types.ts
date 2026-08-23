// FastAPI가 돌려주는 값의 타입. backend/app/harness/contracts.py와 짝을 이룬다.

export type Disclosure = 'PUBLIC' | 'INTERNAL' | 'EMBARGO'

export type RunState =
  | 'CREATED'
  | 'VALIDATING_INPUT'
  | 'NEEDS_INPUT'
  | 'EXTRACTING_FACTS'
  | 'DRAFTING'
  | 'CHECKING_DRAFT'
  | 'REVIEW_READY'
  | 'REVISING'
  | 'CHECKING_REVISION'
  | 'DRAFT_READY'
  | 'FAILED'

export interface SourceRoleOption {
  value: string
  label: string
}

export interface Bootstrap {
  app_title: string
  notice: string
  contract_id: string
  procedure_stage: string
  procedure_stage_label: string
  external_ai: {
    policy_version: string
    provider: string
    model: string
    sent_items: string[]
    notice: string
  }
  limits: {
    max_sources: number
    max_total_chars: number
    purpose_min_chars: number
    purpose_max_chars: number
  }
  source_roles: SourceRoleOption[]
  model_gateway: 'fake' | 'live'
}

export interface Issue {
  issue_id: string
  code: string
  subject: string
  severity: 'BLOCKING' | 'WARNING'
  message: string
  question: string
  source_ids: string[]
  resolution_kind: string
  requires_new_run: boolean
}

export interface RunSourceView {
  source_id: string
  display_name: string
  role: string
  role_label: string
  char_count: number
}

export interface FactView {
  fact_id: string
  kind: string
  subject: string
  value: string
  unit: string
  provenance: string
  source_name: string
  quote: string
  raw_line: number
  raw_column: number
}

export interface RoleChoice {
  candidate_id: string
  source_id: string
  role: string
  role_label: string
  label: string
  evidence_quote: string
}

export interface RunView {
  run_id: string
  state: RunState
  status_label: string
  contract_id: string
  procedure_stage: string
  procedure_stage_label: string
  effect_status_label: string
  basis_date: string
  purpose: string
  disclosure: Disclosure
  announcement_subject_input: string | null
  created_at: string
  updated_at: string
  draft_version: number
  actual_model_calls: number
  max_model_calls: number
  estimated_cost_usd: number
  cost_limit_usd: number
  sources: RunSourceView[]
  issues: Issue[]
  role_choices: RoleChoice[]
  facts: FactView[]
  rejected_evidence: string[]
  failure: {
    kind: string | null
    code: string | null
    message: string | null
    next_action: string | null
  } | null
}

export interface ApiError {
  error_code: string
  message: string
  next_action: string
  run_id: string | null
}

/** 화면에서 편집 중인 자료 한 개. */
export interface SourceDraft {
  key: string
  display_name: string
  text: string
  role: string
}
