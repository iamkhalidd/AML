<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { AlertDetail, Case, ClosureReason } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'
import CustomerPanel from '@/components/CustomerPanel.vue'
import ActivityTimeline, { type TimelineItem } from '@/components/ActivityTimeline.vue'
import { formatDateTime, formatMoney, formatValue, humanize } from '@/format'
import { useSession } from '@/stores/session'

const props = defineProps<{ id: string }>()
const session = useSession()
const router = useRouter()

const alert = ref<AlertDetail | null>(null)
const error = ref<string | null>(null)
const actionError = ref<string | null>(null)
const busy = ref(false)

const note = ref('')
const closing = ref(false)
const reasons = ref<ClosureReason[]>([])
const reason = ref('')
const closeNote = ref('')

const canWork = computed(() => session.can('alerts.work'))
const isOpen = computed(() => alert.value && ['open', 'in_review'].includes(alert.value.status))
const mine = computed(() => alert.value?.assignee?.id === session.user?.id)

const timeline = computed<TimelineItem[]>(() =>
  (alert.value?.timeline ?? []).map((event) => ({
    id: event.id,
    at: event.at,
    kind: event.event_type,
    text: event.event_type === 'note' ? formatValue(event.detail.text) : null,
    detail: event.detail,
    who: event.user?.name ?? null,
  })),
)

function message(caught: unknown): string {
  return caught instanceof Error ? caught.message : 'Something went wrong.'
}

async function load() {
  error.value = null
  try {
    alert.value = await api.get<AlertDetail>(`/v1/alerts/${props.id}`)
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

const claim = () => act(() => api.post(`/v1/alerts/${props.id}/claim`))

async function addNote() {
  const text = note.value.trim()
  if (!text) return
  if (await act(() => api.post(`/v1/alerts/${props.id}/notes`, { text }))) note.value = ''
}

async function openClose() {
  closing.value = true
  if (!reasons.value.length) {
    try {
      reasons.value = (await api.get<{ reasons: ClosureReason[] }>('/v1/alert-closure-reasons')).reasons
    } catch (caught) {
      actionError.value = message(caught)
    }
  }
}

async function close() {
  if (!reason.value || !closeNote.value.trim()) return
  const done = await act(() =>
    api.post(`/v1/alerts/${props.id}/close`, { reason: reason.value, note: closeNote.value.trim() }),
  )
  if (done) {
    closing.value = false
    reason.value = ''
    closeNote.value = ''
  }
}

async function escalate() {
  busy.value = true
  actionError.value = null
  try {
    const created = await api.post<Case>('/v1/cases', { alert_ids: [props.id] })
    await router.push({ name: 'case', params: { id: created.id } })
  } catch (caught) {
    actionError.value = message(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="page">
    <RouterLink :to="{ name: 'alerts' }" class="back">← Alerts</RouterLink>

    <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>
    <p v-else-if="!alert" class="muted">Loading alert…</p>

    <template v-if="alert">
      <header class="head">
        <div class="title">
          <div class="badges">
            <StatusBadge :value="alert.severity" kind="severity" />
            <StatusBadge :value="alert.status" kind="status" />
          </div>
          <h1>{{ alert.rule.name }}</h1>
          <p class="muted">
            {{ alert.customer.name }} · raised {{ formatDateTime(alert.created_at) }} ·
            {{ alert.assignee ? `with ${alert.assignee.name}` : 'unassigned' }}
          </p>
        </div>
        <div v-if="canWork && isOpen" class="actions">
          <button v-if="!mine" class="btn" type="button" :disabled="busy" @click="claim">
            {{ alert.assignee ? 'Take over' : 'Claim' }}
          </button>
          <button class="btn" type="button" :disabled="busy" @click="openClose">Close</button>
          <button
            v-if="session.can('cases.work')"
            class="btn btn-primary"
            type="button"
            :disabled="busy"
            @click="escalate"
          >
            Escalate to case
          </button>
        </div>
      </header>

      <p v-if="actionError" class="notice notice-error" role="alert">{{ actionError }}</p>

      <form v-if="closing" class="panel close-form" @submit.prevent="close">
        <div class="panel-head"><h2>Close this alert</h2></div>
        <div class="panel-body grid">
          <label class="field">
            <span>Reason</span>
            <select v-model="reason" class="select" required>
              <option value="" disabled>Choose a reason</option>
              <option v-for="r in reasons" :key="r.code" :value="r.code">{{ r.label }}</option>
            </select>
          </label>
          <label class="field">
            <span>Why you are closing it</span>
            <textarea v-model="closeNote" class="textarea" required />
          </label>
          <div class="row-actions">
            <button class="btn" type="button" @click="closing = false">Cancel</button>
            <button
              class="btn btn-primary"
              type="submit"
              :disabled="busy || !reason || !closeNote.trim()"
            >
              Close alert
            </button>
          </div>
        </div>
      </form>

      <div class="columns">
        <div class="stack">
          <section class="panel">
            <div class="panel-head"><h2>Why it fired</h2></div>
            <div class="panel-body stack-sm">
              <p class="explain">{{ alert.explanation }}</p>
              <dl v-if="Object.keys(alert.trigger_values).length" class="facts">
                <template v-for="(value, key) in alert.trigger_values" :key="key">
                  <dt>{{ humanize(String(key)) }}</dt>
                  <dd>{{ formatValue(value) }}</dd>
                </template>
              </dl>
              <details class="rule">
                <summary>
                  Rule {{ alert.rule_detail.name }}, version {{ alert.rule_detail.version }}
                </summary>
                <p>{{ alert.rule_detail.rationale }}</p>
                <dl class="facts">
                  <template v-for="(value, key) in alert.rule_detail.parameters" :key="key">
                    <dt>{{ humanize(String(key)) }}</dt>
                    <dd>{{ formatValue(value) }}</dd>
                  </template>
                </dl>
              </details>
            </div>
          </section>

          <section v-if="alert.closure" class="panel">
            <div class="panel-head"><h2>Closure</h2></div>
            <div class="panel-body">
              <dl class="facts">
                <dt>Reason</dt>
                <dd>{{ humanize(alert.closure.reason) }}</dd>
                <dt>Closed</dt>
                <dd>
                  {{ formatDateTime(alert.closure.closed_at) }} by
                  {{ alert.closure.closed_by?.name ?? 'the system' }}
                </dd>
                <dt v-if="alert.closure.note">Note</dt>
                <dd v-if="alert.closure.note">{{ alert.closure.note }}</dd>
              </dl>
            </div>
          </section>

          <section class="panel">
            <div class="panel-head">
              <h2>Transactions</h2>
              <span class="muted">{{ alert.transactions.length }}</span>
            </div>
            <div class="table-scroll">
              <table class="table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Direction</th>
                    <th class="num">Amount</th>
                    <th>Channel</th>
                    <th>Counterparty</th>
                    <th>Narration</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="txn in alert.transactions" :key="txn.id">
                    <td class="nowrap">{{ formatDateTime(txn.occurred_at) }}</td>
                    <td>{{ humanize(txn.direction) }}</td>
                    <td class="num">{{ formatMoney(txn.amount, txn.currency) }}</td>
                    <td>{{ humanize(txn.channel) }}</td>
                    <td>
                      {{ txn.counterparty_name ?? '—' }}
                      <div v-if="txn.counterparty_account" class="muted mono">
                        {{ txn.counterparty_account }}
                      </div>
                    </td>
                    <td class="muted">{{ txn.narration ?? '' }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p v-if="!alert.transactions.length" class="empty">No transactions attached.</p>
          </section>

          <section class="panel">
            <div class="panel-head"><h2>Activity</h2></div>
            <div class="panel-body stack-sm">
              <form v-if="canWork" class="note-form" @submit.prevent="addNote">
                <label class="sr-only" for="alert-note">Add a note</label>
                <textarea
                  id="alert-note"
                  v-model="note"
                  class="textarea"
                  placeholder="Add a note for the record"
                />
                <button class="btn" type="submit" :disabled="busy || !note.trim()">Add note</button>
              </form>
              <ActivityTimeline :items="timeline" />
            </div>
          </section>
        </div>

        <CustomerPanel :view="alert.customer_view" :current-alert-id="alert.id" />
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
  gap: 6px;
  margin-bottom: 8px;
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

.grid {
  display: grid;
  gap: 14px;
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

.stack-sm {
  display: grid;
  gap: 14px;
}

.explain {
  margin: 0;
  font-size: 15px;
}

.rule summary {
  color: var(--muted);
  cursor: pointer;
}

.rule p {
  margin: 8px 0;
}

.nowrap {
  white-space: nowrap;
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
