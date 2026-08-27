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

export interface ClaimTextView {
  text: string
  fact_ids: string[]
  claim_ids: string[]
}

export interface DraftParagraphView {
  paragraph_id: string
  section_kind: string
  text: string
  fact_ids: string[]
  supplementary_rule_ids: string[]
}

export interface DraftView {
  candidate_id: string
  version: number
  draft_label: string
  basis_date: string
  title: ClaimTextView
  key_points: ClaimTextView[]
  lead: ClaimTextView
  paragraphs: DraftParagraphView[]
  contact_text: string
  placeholders: string[]
  claims: { claim_id: string; text: string; fact_ids: string[] }[]
}

export interface FinalTextView {
  rule: string
  source_name: string
  bill_number: string
  body: string
  derivation_id: string
}

export interface SupplementaryRuleView {
  rule_id: string
  kind: string
  applies_to: string
}

export interface ValidationFindingView {
  finding_id: string
  rule_id: string
  rule_document: string
  affected_part: string
  severity: 'BLOCKING' | 'WARNING'
  message: string
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
  final_text: FinalTextView | null
  changed_articles: string[]
  supplementary_rules: SupplementaryRuleView[]
  draft: DraftView | null
  validation_findings: ValidationFindingView[]
  /** 사람이 사실 하나하나를 확인한 기록 (누적 5일차). */
  fact_reviews: FactReviewView[]
  /** 확인하면 보호가 되는 사실. 틀리면 가장 위험한 값들이다. */
  protected_candidate_fact_ids: string[]
  /** 아직 사람이 안 본 사실. 하나라도 있으면 내려받을 수 없다. */
  unreviewed_fact_ids: string[]
  /**
   * 사람이 "다릅니다"를 눌렀는데 초안이 **아직 쓰고 있는** 사실.
   * 하나라도 있으면 내려받을 수 없다 (`M4`).
   */
  wrong_fact_ids_in_use: string[]
  /** 고치기 기록. 실패한 시도도 보여 준다. */
  revision_attempts: RevisionAttemptView[]
  /** 지난 판 번호. 되짚을 수 있어야 한다. */
  previous_versions: number[]
  /** 지금 내려받을 수 있는지. */
  can_download: boolean
  failure: {
    kind: string | null
    code: string | null
    message: string | null
    next_action: string | null
  } | null
}

export interface FactReviewView {
  fact_id: string
  verdict: 'OK' | 'WRONG'
  note: string
}

export interface RevisionAttemptView {
  attempt_id: string
  instruction: string
  outcome: 'APPLIED' | 'REJECTED'
  /** 막은 규칙 코드. 되짚을 때만 쓴다. 사람에게 그대로 보여 주지 않는다. */
  blocking_rule_ids: string[]
  /** 사람이 읽는 이유. 화면은 이것을 보여 준다. */
  blocking_messages: string[]
  resulting_version: number
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
