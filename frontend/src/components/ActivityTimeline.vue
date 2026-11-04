<script setup lang="ts">
import { formatDateTime, formatValue, humanize } from '@/format'

export interface TimelineItem {
  id: number
  at: string
  kind: string
  text: string | null
  detail: Record<string, unknown>
  who: string | null
}

defineProps<{ items: TimelineItem[] }>()

/** Show the detail fields that say something; skip IDs and empty values. */
function details(detail: Record<string, unknown>): [string, string][] {
  return Object.entries(detail)
    .filter(([key, value]) => value !== null && value !== '' && !key.endsWith('_id') && key !== 'id')
    .filter(([, value]) => typeof value !== 'object' || Array.isArray(value))
    .slice(0, 4)
    .map(([key, value]) => [humanize(key), formatValue(value)])
}
</script>

<template>
  <ol v-if="items.length" class="timeline">
    <li v-for="item in items" :key="item.id" :class="{ note: item.text }">
      <div class="line">
        <strong>{{ humanize(item.kind) }}</strong>
        <span class="muted"> · {{ item.who ?? 'System' }} · {{ formatDateTime(item.at) }}</span>
      </div>
      <p v-if="item.text" class="text">{{ item.text }}</p>
      <div v-else-if="details(item.detail).length" class="muted small">
        <span v-for="[key, value] in details(item.detail)" :key="key" class="pair"
          >{{ key }}: {{ value }}</span
        >
      </div>
    </li>
  </ol>
  <p v-else class="muted">Nothing recorded yet.</p>
</template>

<style scoped>
.timeline {
  display: grid;
  gap: 14px;
  margin: 0;
  padding: 0 0 0 18px;
  border-left: 2px solid var(--border);
  list-style: none;
}

.timeline li {
  position: relative;
}

.timeline li::before {
  position: absolute;
  top: 7px;
  left: -24px;
  width: 10px;
  height: 10px;
  border: 2px solid var(--surface);
  border-radius: 50%;
  background: var(--border-strong);
  content: '';
}

.timeline li.note::before {
  background: var(--accent);
}

.text {
  margin: 4px 0 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: #f6f8fa;
  white-space: pre-wrap;
}

.small {
  font-size: 12.5px;
}

.pair + .pair::before {
  content: ' · ';
}
</style>
