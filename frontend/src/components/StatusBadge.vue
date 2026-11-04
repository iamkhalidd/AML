<script setup lang="ts">
import { computed } from 'vue'

import { humanize } from '@/format'

/** A severity or status label. Colour is never the only signal: the word is always shown. */
const props = defineProps<{ value: string | null | undefined; kind?: 'severity' | 'status' }>()

const tone = computed(() => {
  const value = props.value ?? ''
  if (props.kind === 'severity') {
    return { high: 'danger', medium: 'warn', low: 'low' }[value] ?? 'neutral'
  }
  return (
    {
      open: 'info',
      in_review: 'warn',
      escalated: 'low',
      closed: 'neutral',
      pending: 'warn',
      approved: 'info',
      rejected: 'danger',
    }[value] ?? 'neutral'
  )
})
</script>

<template>
  <span class="badge" :class="[`tone-${tone}`, { dot: kind === 'severity' }]">{{
    humanize(value)
  }}</span>
</template>

<style scoped>
.badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  white-space: nowrap;
}

.dot::before {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentcolor;
  content: '';
}

.tone-danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.tone-warn {
  background: var(--warn-soft);
  color: var(--warn);
}

.tone-low {
  background: var(--low-soft);
  color: var(--low);
}

.tone-info {
  background: var(--accent-soft);
  color: var(--accent-strong);
}

.tone-neutral {
  background: #eef1f4;
  color: var(--muted);
}
</style>
