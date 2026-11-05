/** Shapes returned by the AML API (see the backend's OpenAPI document). */

export type Severity = 'low' | 'medium' | 'high'
export type AlertStatus = 'open' | 'in_review' | 'escalated' | 'closed'
export type CaseStatus = 'open' | 'closed'
export type CaseOutcome = 'no_suspicion' | 'reported' | 'referred'

export interface UserRef {
  id: string
  name: string
}

export interface Me {
  id: string
  email: string
  full_name: string
  role: string
  permissions: string[]
}

export interface CustomerRef {
  id: string
  external_id: string
  name: string
  customer_type: string
  risk_level?: Severity | null
}

export interface RuleRef {
  id: string
  name: string
  rule_type: string
  version_id: string
  version: number
}

export interface Amount {
  value: number
  currency: string | null
}

export interface Alert {
  id: string
  severity: Severity
  status: AlertStatus
  customer: CustomerRef
  rule: RuleRef
  explanation: string
  amount: Amount | null
  hits: number
  assignee: UserRef | null
  window_start: string | null
  window_end: string | null
  created_at: string
  updated_at: string
}

export interface AlertPage {
  items: Alert[]
  next_cursor: string | null
  counts: { open: number; mine: number; overdue: number | null }
}

export interface Transaction {
  id: string
  external_id: string
  account_external_id: string
  occurred_at: string
  amount: number
  currency: string
  direction: string
  channel: string
  txn_type: string
  status: string
  counterparty_name: string | null
  counterparty_account: string | null
  counterparty_bank_code: string | null
  narration: string | null
}

export interface AlertEvent {
  id: number
  at: string
  event_type: string
  detail: Record<string, unknown>
  user: UserRef | null
}

export interface CustomerProfile {
  id: string
  external_id: string
  customer_type: string
  full_name: string
  date_of_birth: string | null
  nationality: string | null
  occupation: string | null
  industry: string | null
  state: string | null
  country: string | null
  onboarding_date: string
  kyc_status: string
  risk_level: Severity | null
  feed_risk_level: Severity | null
  is_pep: boolean
  segment: string | null
  status: string
  registration_number: string | null
}

export interface CustomerView {
  customer: CustomerProfile
  expected_activity: {
    declared_income_min: number | null
    declared_income_max: number | null
    monthly_credit: number | null
    monthly_credit_count: number | null
  }
  actual_activity: { currency: string; credit_count: number; credit_total: number }[]
  activity_window_days: number
  beneficial_owners: {
    owner_ref: string
    full_name: string
    ownership_percent: string | null
    control_type: string
    is_pep: boolean
  }[]
  accounts: {
    id: string
    external_id: string
    product_type: string
    currency: string
    opened_date: string
    status: string
  }[]
  earlier_alerts: {
    id: string
    severity: Severity
    status: AlertStatus
    rule_name: string
    created_at: string
    closure_reason: string | null
  }[]
  cases: {
    id: string
    reference: string
    title: string
    status: CaseStatus
    outcome: CaseOutcome | null
    opened_at: string
  }[]
}

export interface AlertDetail extends Alert {
  rule_detail: {
    name: string
    rule_type: string
    version: number
    severity: Severity
    rationale: string
    parameters: Record<string, unknown>
  }
  trigger_values: Record<string, unknown>
  transactions: Transaction[]
  notes: AlertEvent[]
  timeline: AlertEvent[]
  closure: {
    closed_at: string
    closed_by: UserRef | null
    reason: string
    note: string | null
    mode: 'manual' | 'auto'
  } | null
  customer_view: CustomerView
}

export interface ClosureReason {
  code: string
  label: string
}

export interface Case {
  id: string
  reference: string
  title: string
  status: CaseStatus
  severity: Severity | null
  alert_count: number
  customer: CustomerRef
  assignee: UserRef | null
  opened_by: UserRef
  opened_at: string
  updated_at: string
  closure_pending: boolean
  outcome: CaseOutcome | null
  outcome_reason: string | null
  closed_at: string | null
}

export interface CasePage {
  items: Case[]
  next_cursor: string | null
}

export interface CaseEntry {
  id: number
  at: string
  entry_type: string
  body: string | null
  detail: Record<string, unknown>
  corrects: number | null
  user: UserRef | null
}

export interface CaseWorkspace extends Case {
  alerts: {
    id: string
    severity: Severity
    status: string
    explanation: string
    rule_name: string
    rule_type: string
    txn_count: number
    created_at: string
  }[]
  timeline: CaseEntry[]
  attachments: {
    id: string
    filename: string
    size_bytes: number
    uploaded_by: UserRef
    uploaded_at: string
  }[]
  closure_requests: {
    approval_id: string
    status: string
    outcome: CaseOutcome
    reason: string
    requested_by: UserRef
    requested_at: string
    decided_by: UserRef | null
    decision_note: string | null
  }[]
  customer_view: CustomerView
}

export type ApprovalKind = 'case_closure' | 'report_submission' | 'rule_change'
export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'withdrawn'

export interface Approval {
  id: string
  kind: ApprovalKind
  subject_id: string
  subject_label: string
  payload: Record<string, unknown>
  rationale: string
  status: ApprovalStatus
  maker_id: string
  maker_name: string
  created_at: string
  checker_id: string | null
  checker_name: string | null
  decided_at: string | null
  decision_note: string | null
  can_decide: boolean
  blocked_reason: string | null
}

export interface ReportForApproval {
  id: string
  reference: string
  report_type: string
  status: string
  case_id: string | null
  case_reference: string | null
  customer: CustomerRef
  created_by: UserRef
  narrative: string
  transactions: { id: string }[]
}

export interface RuleForApproval {
  id: string
  name: string
  current_version: number | null
  versions: {
    version: number
    parameters: Record<string, unknown>
    severity: string
    effective_from: string
  }[]
}
