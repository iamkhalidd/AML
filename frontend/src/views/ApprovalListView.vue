<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { Approval } from '@/api/types'
import StatusBadge from '@/components/StatusBadge.vue'
import { approvalKindLabel, approvalStatusLabel } from '@/approvals'
import { age, formatDateTime } from '@/format'

const route = useRoute()
const router = useRouter()

const TABS = ['pending', 'approved', 'rejected', 'withdrawn'] as const

const status = computed(() => TABS.find((tab) => tab === route.query.status) ?? 'pending')

const items = ref<Approval[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

async function load() {
  loading.value = true
  error.value = null
  try {
    items.value = await api.get<Approval[]>('/v1/approvals', { status: status.value, limit: 200 })
  } catch (caught) {
    items.value = []
    error.value = caught instanceof Error ? caught.message : 'Could not load approvals.'
  } finally {
    loading.value = false
  }
}

watch(status, load, { immediate: true })

/** Longest wait on top while waiting; the API gives newest first. */
const rows = computed(() => (status.value === 'pending' ? [...items.value].reverse() : items.value))
</script>

<template>
  <div class="page">
    <header>
      <h1>Approvals</h1>
      <p class="muted">Changes that need a second person before they take effect.</p>
    </header>

    <div class="tabs" role="tablist">
      <button
        v-for="tab in TABS"
        :key="tab"
        type="button"
        role="tab"
        class="tab"
        :aria-selected="status === tab"
        @click="router.replace({ query: tab === 'pending' ? {} : { status: tab } })"
      >
        {{ approvalStatusLabel(tab) }}
      </button>
    </div>

    <p v-if="error" class="notice notice-error" role="alert">{{ error }}</p>

    <section v-else class="panel">
      <div v-if="rows.length" class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>Change</th>
              <th>About</th>
              <th>Asked by</th>
              <th>Reason given</th>
              <th>{{ status === 'pending' ? 'Waiting' : 'Decided' }}</th>
              <th>{{ status === 'pending' ? 'For you' : 'Status' }}</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in rows"
              :key="item.id"
              class="row"
              @click="router.push({ name: 'approval', params: { id: item.id } })"
            >
              <td class="nowrap">
                <RouterLink :to="{ name: 'approval', params: { id: item.id } }" @click.stop>
                  {{ approvalKindLabel(item.kind) }}
                </RouterLink>
              </td>
              <td>{{ item.subject_label }}</td>
              <td class="nowrap">{{ item.maker_name }}</td>
              <td class="muted"><span class="reason">{{ item.rationale }}</span></td>
              <td v-if="status === 'pending'" class="nowrap" :title="formatDateTime(item.created_at)">
                {{ age(item.created_at) }}
              </td>
              <td v-else class="nowrap">
                {{ formatDateTime(item.decided_at) }}
                <div v-if="item.checker_name" class="muted">{{ item.checker_name }}</div>
              </td>
              <td class="nowrap">
                <template v-if="status === 'pending'">
                  <strong v-if="item.can_decide">Needs your decision</strong>
                  <span v-else class="muted">With someone else</span>
                </template>
                <StatusBadge v-else :value="item.status" kind="status" />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else-if="loading" class="empty">Loading approvals…</p>
      <p v-else class="empty">
        {{ status === 'pending' ? 'Nothing is waiting for approval.' : 'Nothing here yet.' }}
      </p>
    </section>
  </div>
</template>

<style scoped>
.page {
  display: grid;
  gap: 16px;
}

header p {
  margin: 4px 0 0;
}

.tabs {
  display: inline-flex;
  justify-self: start;
  padding: 3px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
}

.tab {
  padding: 5px 14px;
  border: 0;
  border-radius: 6px;
  background: none;
  color: var(--muted);
  font: inherit;
  font-weight: 500;
  cursor: pointer;
}

.tab[aria-selected='true'] {
  background: var(--ink);
  color: #fff;
}

.row {
  cursor: pointer;
}

.nowrap {
  white-space: nowrap;
}

.reason {
  display: -webkit-box;
  overflow: hidden;
  max-width: 340px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  line-clamp: 2;
}
</style>
