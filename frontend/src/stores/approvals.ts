import { defineStore } from 'pinia'
import { ref } from 'vue'

import { api } from '@/api/client'
import type { Approval } from '@/api/types'

/** How many pending changes the signed-in user could decide: the number beside Approvals. */
export const useApprovals = defineStore('approvals', () => {
  const waiting = ref(0)

  async function refresh(): Promise<void> {
    try {
      const pending = await api.get<Approval[]>('/v1/approvals', { status: 'pending', limit: 500 })
      waiting.value = pending.filter((item) => item.can_decide).length
    } catch {
      // Only a hint in the sidebar; the Approvals page reports its own errors.
    }
  }

  return { waiting, refresh }
})
