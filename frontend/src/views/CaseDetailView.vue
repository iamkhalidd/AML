<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'

import { api } from '@/api/client'
import type { CaseOutcome, CaseWorkspace } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'
import CustomerPanel from '@/components/CustomerPanel.vue'
import ActivityTimeline, { type TimelineItem } from '@/components/ActivityTimeline.vue'
import { formatDate, formatDateTime, humanize } from '@/format'
import { useSession } from '@/stores/session'

const props = defineProps<{ id: string }>()
const session = useSession()

const workspace = ref<CaseWorkspace | null>(null)
const error = ref<string | null>(null)
const actionError = ref<string | null>(null)
const busy = ref(false)

const note = ref('')
const requesting = ref(false)
const outcome = ref<CaseOutcome | ''>('')
const reason = ref('')

const outcomes: { value: CaseOutcome; label: string }[] = [
  { value: 'no_suspicion', label: 'No suspicion found' },
  { value: 'reported', label: 'Reported to the NFIU' },
  { value: 'referred', label: 'Referred elsewhere' },
]

const isOpen = computed(() => workspace.value?.status === 'open')
const canWork = computed(() => session.can('cases.work'))
const mine = computed(() => workspace.value?.assignee?.id === session.user?.id)
const canRequestClosure = computed(
  () => isOpen.value && session.can('cases.request_closure') && !workspace.value?.closure_pending,
)

const timeline = computed<TimelineItem[]>(() =>
  [...(workspace.value?.timeline ?? [])].reverse().map((entry) => ({
    id: entry.id,
    at: entry.at,
    kind: entry.corrects ? 'correction' : entry.entry_type,
    text: entry.body,
    detail: entry.detail,
    who: entry.user?.name ?? null,
  })),
)

function message(caught: unknown): string {
  return caught instanceof Error ? caught.message : 'Something went wrong.'
}

async function load() {
  error.value = null
  try {
    workspace.value = await api.get<CaseWorkspace>(`/v1/cases/${props.id}`)
  } catch (caught) {
    error.value = message(caught)
  }
}

watch(() => props.id, load, { immediate: true })

async function act(run: () => Promise<unknown>) {
  busy.value = true
  actionError.value = null
  try {
    await run()
    await load()
    return true
  } catch (caught) {
    actionError.value = message(caught)
    return false
  } finally {
    busy.value = false
  }
}

const claim = () => act(() => api.post(`/v1/cases/${props.id}/claim`))

async function addNote() {
  const text = note.value.trim()
  if (!text) return
  if (await act(() => api.post(`/v1/cases/${props.id}/notes`, { text }))) note.value = ''
}

async function requestClosure() {
  if (!outcome.value || !reason.value.trim()) return
  const done = await act(() =>
    api.post(`/v1/cases/${props.id}/closure-request`, {
      outcome: outcome.value,
      reason: reason.value.trim(),
    }),
  )
  if (done) {
    requesting.value = false
    outcome.value = ''
    reason.value = ''
  }
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
</script>

<template>
  <div class="page">
    <RouterLink :to="{ name: 'cases' }" class="back">← Cases</RouterLink>

    <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>
    <p v-else-if="!workspace" class="muted">Loading case…</p>

    <template v-if="workspace">
      <header class="head">
        <div class="title">
          <div class="badges">
            <span class="mono ref">{{ workspace.reference }}</span>
            <StatusBadge v-if="workspace.severity" :value="workspace.severity" kind="severity" />
            <StatusBadge :value="workspace.closure_pending ? 'pending' : workspace.status" kind="status" />
          </div>
          <h1>{{ workspace.title }}</h1>
          <p class="muted">
            {{ workspace.customer.name }} · opened {{ formatDate(workspace.opened_at) }} by
            {{ workspace.opened_by.name }} ·
            {{ workspace.assignee ? `with ${workspace.assignee.name}` : 'unassigned' }}
          </p>
        </div>
        <div v-if="isOpen" class="actions">
          <button v-if="canWork && !mine" class="btn" type="button" :disabled="busy" @click="claim">
            {{ workspace.assignee ? 'Take over' : 'Claim' }}
          </button>
          <button
            v-if="canRequestClosure"
            class="btn btn-primary"
            type="button"
            :disabled="busy"
            @click="requesting = true"
          >
            Request closure
          </button>
        </div>
      </header>

      <p v-if="actionError" class="notice notice-error" role="alert">{{ actionError }}</p>
      <p v-if="workspace.closure_pending" class="notice notice-info">
        Closure is waiting for a second person to approve it.
      </p>
      <p v-if="workspace.outcome" class="notice notice-info">
        Closed {{ formatDateTime(workspace.closed_at) }}: {{ humanize(workspace.outcome) }}.
        {{ workspace.outcome_reason }}
      </p>

      <form v-if="requesting" class="panel" @submit.prevent="requestClosure">
        <div class="panel-head"><h2>Request closure</h2></div>
        <div class="panel-body grid">
          <fieldset class="choices">
            <legend>Outcome</legend>
            <label v-for="o in outcomes" :key="o.value" class="choice">
              <input v-model="outcome" type="radio" name="outcome" :value="o.value" />
              {{ o.label }}
            </label>
          </fieldset>
          <label class="field">
            <span>Reason for the approver</span>
            <textarea v-model="reason" class="textarea" required />
          </label>
          <div class="row-actions">
            <button class="btn" type="button" @click="requesting = false">Cancel</button>
            <button
              class="btn btn-primary"
              type="submit"
              :disabled="busy || !outcome || !reason.trim()"
            >
              Send for approval
            </button>
          </div>
        </div>
      </form>

      <div class="columns">
        <div class="stack">
          <section class="panel">
            <div class="panel-head">
              <h2>Alerts in this case</h2>
              <span class="muted">{{ workspace.alerts.length }}</span>
            </div>
            <ul class="alerts">
              <li v-for="item in workspace.alerts" :key="item.id">
                <div class="alert-line">
                  <StatusBadge :value="item.severity" kind="severity" />
                  <RouterLink :to="{ name: 'alert', params: { id: item.id } }">{{
                    item.rule_name
                  }}</RouterLink>
                  <span class="muted">
                    · {{ item.txn_count }} txns · {{ formatDate(item.created_at) }}</span
                  >
                </div>
                <p class="muted">{{ item.explanation }}</p>
              </li>
            </ul>
          </section>

          <section v-if="workspace.closure_requests.length" class="panel">
            <div class="panel-head"><h2>Closure requests</h2></div>
            <div class="table-scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th>Requested</th>
                    <th>Outcome</th>
                    <th>Reason</th>
                    <th>Decision</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in workspace.closure_requests" :key="r.approval_id">
                    <td>
                      {{ r.requested_by.name }}
                      <div class="muted">{{ formatDateTime(r.requested_at) }}</div>
                    </td>
                    <td>{{ humanize(r.outcome) }}</td>
                    <td>{{ r.reason }}</td>
                    <td>
                      <StatusBadge :value="r.status" kind="status" />
                      <div v-if="session.can('approvals.view')">
                        <RouterLink :to="{ name: 'approval', params: { id: r.approval_id } }">
                          Open approval
                        </RouterLink>
                      </div>
                      <div v-if="r.decided_by" class="muted">{{ r.decided_by.name }}</div>
                      <div v-if="r.decision_note" class="muted">{{ r.decision_note }}</div>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head"><h2>Investigation log</h2></div>
            <div class="panel-body grid">
              <form v-if="canWork && isOpen" class="note-form" @submit.prevent="addNote">
                <label class="sr-only" for="case-note">Add a note</label>
                <textarea
                  id="case-note"
                  v-model="note"
                  class="textarea"
                  placeholder="What you checked, found or decided"
                />
                <button class="btn" type="submit" :disabled="busy || !note.trim()">Add note</button>
              </form>
              <ActivityTimeline :items="timeline" />
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>Attachments</h2>
              <span class="muted">{{ workspace.attachments.length }}</span>
            </div>
            <ul v-if="workspace.attachments.length" class="files">
              <li v-for="file in workspace.attachments" :key="file.id">
                <a :href="`/v1/cases/${workspace.id}/attachments/${file.id}`">{{ file.filename }}</a>
                <span class="muted">
                  · {{ size(file.size_bytes) }} · {{ file.uploaded_by.name }} ·
                  {{ formatDate(file.uploaded_at) }}</span
                >
              </li>
            </ul>
            <p v-else class="empty">No attachments.</p>
          </section>
        </div>

        <CustomerPanel :view="workspace.customer_view" />
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 16px;
}

.back {
  color: var(--muted);
  text-decoration: none;
}

.back:hover {
  color: var(--text);
}

.head {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}

.title p {
  margin: 4px 0 0;
}

.badges {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.ref {
  color: var(--muted);
  font-weight: 600;
}

.actions,
.row-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.row-actions {
  justify-content: flex-end;
}

.notice {
  margin: 0;
}

.grid {
  display: grid;
  gap: 14px;
}

.choices {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  border: 0;
}

.choices legend {
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 500;
}

.choice {
  display: flex;
  align-items: center;
  gap: 8px;
}

.columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 340px;
  gap: 16px;
  align-items: start;
}

.stack {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.alerts,
.files {
  margin: 0;
  padding: 0;
  list-style: none;
}

.alerts li,
.files li {
  padding: 12px 16px;
  border-bottom: 1px solid #edf0f3;
}

.alerts li:last-child,
.files li:last-child {
  border-bottom: 0;
}

.alert-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.alerts p {
  margin: 4px 0 0;
  font-size: 13px;
}

.note-form {
  display: grid;
  gap: 8px;
  justify-items: end;
}

@media (max-width: 1100px) {
  .columns {
    grid-template-columns: 1fr;
  }
}
</style>
