import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import type { Approval } from '@/api/types'
import { compareParameters } from '@/approvals'
import { useSession } from '@/stores/session'
import ApprovalDetailView from '@/views/ApprovalDetailView.vue'

const base: Approval = {
  id: 'a1',
  kind: 'case_closure',
  subject_id: 'c1',
  subject_label: 'CASE-000001: Cash structuring',
  payload: { outcome: 'no_suspicion', reason: 'Salary explained it' },
  rationale: 'Reviewed the statements',
  status: 'pending',
  maker_id: 'analyst',
  maker_name: 'Ada Analyst',
  created_at: '2026-11-04T10:00:00Z',
  checker_id: null,
  checker_name: null,
  decided_at: null,
  decision_note: null,
  can_decide: true,
  blocked_reason: null,
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function stubApi(approval: Approval, onPost: (url: string, body: unknown) => Response) {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') return onPost(url, JSON.parse(String(init.body ?? 'null')))
    if (url.includes('/v1/cases/')) return json({}, 403)
    if (url.includes('/v1/approvals/')) return json(approval)
    return json([])
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/approvals', name: 'approvals', component: { template: '<p>list</p>' } },
      { path: '/approvals/:id', name: 'approval', component: ApprovalDetailView, props: true },
      { path: '/cases/:id', name: 'case', component: { template: '<p>case</p>' } },
    ],
  })
  await router.push('/approvals/a1')
  const wrapper = mount(ApprovalDetailView, {
    props: { id: 'a1' },
    global: { plugins: [router] },
  })
  await flushPromises()
  return wrapper
}

function button(wrapper: Awaited<ReturnType<typeof mountView>>, text: string) {
  const found = wrapper.findAll('button').find((item) => item.text() === text)
  if (!found) throw new Error(`No button: ${text}`)
  return found
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('ApprovalDetailView', () => {
  it('says what approving does, then shows the result', async () => {
    const fetchMock = stubApi(base, () =>
      json({ ...base, status: 'approved', can_decide: false, checker_name: 'Olu Officer' }),
    )
    const wrapper = await mountView()

    await button(wrapper, 'Approve').trigger('click')
    expect(wrapper.text()).toContain('It cannot be undone')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(String(post?.[0])).toContain('/v1/approvals/a1/approve')
    expect(wrapper.text()).toContain('now approved')
    expect(wrapper.text()).toContain('Olu Officer')
  })

  it('needs a reason to send back', async () => {
    const fetchMock = stubApi(base, () => json({ ...base, status: 'rejected' }))
    const wrapper = await mountView()

    await button(wrapper, 'Send back').trigger('click')
    expect(button(wrapper, 'Send back').attributes('disabled')).toBeDefined()
    await wrapper.find('textarea').setValue('Check the March statements')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({ note: 'Check the March statements' })
  })

  it('explains why someone cannot decide and hides the buttons', async () => {
    stubApi({ ...base, can_decide: false, blocked_reason: 'Your role cannot decide on this.' }, () =>
      json({}),
    )
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('Your role cannot decide on this.')
    expect(wrapper.text()).not.toContain('Approve')
  })

  it('lets the person who asked withdraw', async () => {
    stubApi({ ...base, can_decide: false, blocked_reason: 'You asked for this.' }, () => json({}))
    useSession().user = { id: 'analyst' } as ReturnType<typeof useSession>['user']
    const wrapper = await mountView()

    expect(button(wrapper, 'Withdraw request').exists()).toBe(true)
  })

  it('shows the server refusal and keeps the form open', async () => {
    stubApi(base, () =>
      json({ code: 'SELF_APPROVAL', title: 'The person who holds the case cannot approve it' }, 403),
    )
    const wrapper = await mountView()

    await button(wrapper, 'Approve').trigger('click')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[role=alert]').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(true)
  })
})

describe('compareParameters', () => {
  it('puts changed settings first', () => {
    const rows = compareParameters({ a: 1, b: 2 }, { a: 1, b: 3, c: 4 })
    expect(rows.map((row) => [row.key, row.changed])).toEqual([
      ['b', true],
      ['c', true],
      ['a', false],
    ])
  })
})
