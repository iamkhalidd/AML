import type { ApprovalKind, ApprovalStatus } from '@/api/types'

const KINDS: Record<ApprovalKind, string> = {
  case_closure: 'Case closure',
  report_submission: 'Report',
  rule_change: 'Rule change',
}

const STATUSES: Record<ApprovalStatus, string> = {
  pending: 'Waiting',
  approved: 'Approved',
  rejected: 'Sent back',
  withdrawn: 'Withdrawn',
}

const EFFECTS: Record<ApprovalKind, string> = {
  case_closure: 'This closes the case and all its alerts. It cannot be undone.',
  report_submission: 'This approves the report so it can be filed. It cannot be undone.',
  rule_change: 'This puts the new rule version into effect from its start date.',
}

export function approvalKindLabel(kind: ApprovalKind): string {
  return KINDS[kind] ?? kind
}

export function approvalStatusLabel(status: ApprovalStatus): string {
  return STATUSES[status] ?? status
}

export function approvalEffect(kind: ApprovalKind): string {
  return EFFECTS[kind] ?? 'This applies the change.'
}

export interface ParameterChange {
  key: string
  before: unknown
  after: unknown
  changed: boolean
}

/** Every parameter in either version, side by side, changed ones first. */
export function compareParameters(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
): ParameterChange[] {
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
  const rows = keys.map((key) => ({
    key,
    before: before[key],
    after: after[key],
    changed: JSON.stringify(before[key]) !== JSON.stringify(after[key]),
  }))
  return [...rows.filter((row) => row.changed), ...rows.filter((row) => !row.changed)]
}
