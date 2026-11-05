<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import type { Approval, CaseWorkspace, ReportForApproval, RuleForApproval } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'
import {
  approvalEffect,
  approvalKindLabel,
  approvalStatusLabel,
  compareParameters,
} from '@/approvals'
import { fieldLabel, formatDateTime, formatField, formatValue, humanize } from '@/format'
import { useApprovals } from '@/stores/approvals'
import { useSession } from '@/stores/session'

const props = defineProps<{ id: string }>()
const session = useSession()
const waiting = useApprovals()

const approval = ref<Approval | null>(null)
const error = ref<string | null>(null)
const actionError = ref<string | null>(null)
const busy = ref(false)

const caseView = ref<CaseWorkspace | null>(null)
const report = ref<ReportForApproval | null>(null)
const rule = ref<RuleForApproval | null>(null)
const subjectError = ref(false)

type Step = 'approve' | 'reject' | 'withdraw'
const step = ref<Step | null>(null)
const note = ref('')
const justDecided = ref(false)

function message(caught: unknown): string {
  return caught instanceof Error ? caught.message : 'Something went wrong.'
}

/** The subject gives the approver context; a role that cannot read it can still decide. */
async function loadSubject(item: Approval) {
  subjectError.value = false
  caseView.value = report.value = rule.value = null
  try {
    if (item.kind === 'case_closure') {
      caseView.value = await api.get<CaseWorkspace>(`/v1/cases/${item.subject_id}`)
    } else if (item.kind === 'report_submission') {
      report.value = await api.get<ReportForApproval>(`/v1/reports/${item.subject_id}`)
    } else {
      rule.value = await api.get<RuleForApproval>(`/v1/rules/${item.subject_id}`)
    }
  } catch {
    subjectError.value = true
  }
}

async function load() {
  error.value = null
  approval.value = null
  step.value = null
  justDecided.value = false
  try {
    approval.value = await api.get<Approval>(`/v1/approvals/${props.id}`)
    await loadSubject(approval.value)
  } catch (caught) {
    error.value = message(caught)
  }
}

watch(() => props.id, load, { immediate: true })

const pending = computed(() => approval.value?.status === 'pending')
const mine = computed(() => approval.value?.maker_id === session.user?.id)

const changes = computed(() => {
  const payload = approval.value?.payload
  if (!payload || approval.value?.kind !== 'rule_change') return []
  const current = rule.value?.versions.find((v) => v.version === rule.value?.current_version)
  return compareParameters(
    current ? { severity: current.severity, ...current.parameters } : {},
    { severity: payload.severity, ...(payload.parameters as Record<string, unknown>) },
  )
})

function open(next: Step) {
  step.value = next
  note.value = ''
  actionError.value = null
}

async function decide() {
  if (!step.value) return
  const text = note.value.trim()
  if (step.value === 'reject' && !text) return
  const body = step.value === 'withdraw' ? undefined : { note: text || null }
  busy.value = true
  actionError.value = null
  try {
    approval.value = await api.post<Approval>(`/v1/approvals/${props.id}/${step.value}`, body)
    step.value = null
    justDecided.value = true
    await Promise.all([waiting.refresh(), loadSubject(approval.value)])
  } catch (caught) {
    actionError.value = message(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <RouterLink :to="{ name: 'approvals' }" class="back">← Approvals</RouterLink>

    <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>
    <p v-else-if="!approval" class="muted">Loading approval…</p>

    <template v-if="approval">
      <header>
        <div class="badges">
          <StatusBadge :value="approval.status" kind="status" />
        </div>
        <h1>{{ approvalKindLabel(approval.kind) }}: {{ approval.subject_label }}</h1>
        <p class="muted">
          Asked by {{ approval.maker_name }} · {{ formatDateTime(approval.created_at) }}
        </p>
      </header>

      <p v-if="justDecided" class="notice notice-info" role="status">
        Done. This change is now {{ approvalStatusLabel(approval.status).toLowerCase() }}.
      </p>
      <p v-if="actionError" class="notice notice-error" role="alert">{{ actionError }}</p>

      <section class="panel">
        <div class="panel-head"><h2>Reason given</h2></div>
        <div class="panel-body">
          <p class="text">{{ approval.rationale }}</p>
        </div>
      </section>

      <p v-if="subjectError" class="notice notice-info">
        The details behind this change could not be shown.
      </p>

      <section v-if="approval.kind === 'case_closure'" class="panel">
        <div class="panel-head"><h2>What would close</h2></div>
        <div class="panel-body">
          <dl class="facts">
            <template v-if="caseView">
              <dt>Case</dt>
              <dd>
                <RouterLink :to="{ name: 'case', params: { id: caseView.id } }">{{
                  caseView.reference
                }}</RouterLink>
                · {{ caseView.title }}
              </dd>
              <dt>Customer</dt>
              <dd>{{ caseView.customer.name }}</dd>
              <dt>Severity</dt>
              <dd><StatusBadge :value="caseView.severity" kind="severity" /></dd>
              <dt>Alerts</dt>
              <dd>{{ caseView.alert_count }}</dd>
            </template>
            <dt>Proposed outcome</dt>
            <dd>{{ humanize(formatValue(approval.payload.outcome)) }}</dd>
            <dt>Reason for the outcome</dt>
            <dd>{{ formatValue(approval.payload.reason) }}</dd>
          </dl>
        </div>
      </section>

      <section v-if="report" class="panel">
        <div class="panel-head"><h2>The report</h2></div>
        <div class="panel-body grid">
          <dl class="facts">
            <dt>Reference</dt>
            <dd>{{ report.reference }}</dd>
            <dt>Type</dt>
            <dd>{{ report.report_type.toUpperCase() }}</dd>
            <dt>Customer</dt>
            <dd>{{ report.customer.name }}</dd>
            <dt>Drafted by</dt>
            <dd>{{ report.created_by.name }}</dd>
            <dt v-if="report.case_id">Case</dt>
            <dd v-if="report.case_id">
              <RouterLink :to="{ name: 'case', params: { id: report.case_id } }">{{
                report.case_reference
              }}</RouterLink>
            </dd>
            <dt>Transactions</dt>
            <dd>{{ report.transactions.length }}</dd>
          </dl>
          <div>
            <h3>Narrative</h3>
            <p class="text">{{ report.narrative || 'No narrative written.' }}</p>
          </div>
        </div>
      </section>

      <section v-if="approval.kind === 'rule_change'" class="panel">
        <div class="panel-head">
          <h2>What would change</h2>
          <span class="muted">
            Version {{ formatValue(approval.payload.version) }} · from
            {{ formatDateTime(approval.payload.effective_from as string) }}
          </span>
        </div>
        <div class="table-scroll">
          <table class="table">
            <thead>
              <tr>
                <th>Setting</th>
                <th>In effect now</th>
                <th>Proposed</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in changes" :key="row.key" :class="{ changed: row.changed }">
                <td>{{ fieldLabel(row.key) }}</td>
                <td>{{ row.before === undefined ? '—' : formatField(row.key, row.before) }}</td>
                <td>
                  {{ row.after === undefined ? '—' : formatField(row.key, row.after) }}
                  <span v-if="row.changed" class="muted"> · changed</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <section v-if="!pending" class="panel">
        <div class="panel-head"><h2>Decision</h2></div>
        <div class="panel-body">
          <dl class="facts">
            <dt>Result</dt>
            <dd>{{ approvalStatusLabel(approval.status) }}</dd>
            <dt v-if="approval.checker_name">Decided by</dt>
            <dd v-if="approval.checker_name">{{ approval.checker_name }}</dd>
            <dt>When</dt>
            <dd>{{ formatDateTime(approval.decided_at) }}</dd>
            <dt v-if="approval.decision_note">Note</dt>
            <dd v-if="approval.decision_note">{{ approval.decision_note }}</dd>
          </dl>
        </div>
      </section>

      <section v-else class="panel">
        <div class="panel-head"><h2>Your decision</h2></div>
        <div class="panel-body grid">
          <p v-if="approval.blocked_reason" class="muted reason">{{ approval.blocked_reason }}</p>

          <div v-if="!step" class="actions">
            <template v-if="approval.can_decide">
              <button class="btn btn-primary" type="button" @click="open('approve')">Approve</button>
              <button class="btn" type="button" @click="open('reject')">Send back</button>
            </template>
            <button v-if="mine" class="btn" type="button" @click="open('withdraw')">
              Withdraw request
            </button>
          </div>

          <form v-else class="grid" @submit.prevent="decide">
            <p v-if="step === 'approve'" class="confirm">{{ approvalEffect(approval.kind) }}</p>
            <p v-if="step === 'withdraw'" class="confirm">
              This cancels your request. You can send a new one later.
            </p>
            <label v-if="step !== 'withdraw'" class="field">
              <span>{{ step === 'reject' ? 'Why are you sending it back?' : 'Note (optional)' }}</span>
              <textarea v-model="note" class="textarea" :required="step === 'reject'" />
            </label>
            <div class="actions">
              <button class="btn" type="button" :disabled="busy" @click="step = null">Cancel</button>
              <button
                class="btn"
                :class="step === 'approve' ? 'btn-primary' : 'btn-danger'"
                type="submit"
                :disabled="busy || (step === 'reject' && !note.trim())"
              >
                {{
                  step === 'approve'
                    ? 'Yes, approve'
                    : step === 'reject'
                      ? 'Send back'
                      : 'Yes, withdraw'
                }}
              </button>
            </div>
          </form>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 16px;
  max-width: 860px;
}

.back {
  color: var(--muted);
  text-decoration: none;
}

.back:hover {
  color: var(--text);
}

header p {
  margin: 4px 0 0;
}

.badges {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}

.grid {
  display: grid;
  gap: 14px;
}

.text {
  margin: 0;
  white-space: pre-wrap;
}

.reason,
.confirm {
  margin: 0;
}

.confirm {
  font-weight: 500;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.changed td {
  font-weight: 600;
}
</style>
