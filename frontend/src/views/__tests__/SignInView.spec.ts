import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'

import SignInView from '@/views/SignInView.vue'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/sign-in', name: 'sign-in', component: SignInView },
      { path: '/alerts', name: 'alerts', component: { template: '<p>queue</p>' } },
    ],
  })
}

async function mountAt(path: string) {
  const router = makeRouter()
  await router.push(path)
  const wrapper = mount(SignInView, { global: { plugins: [router] } })
  return { wrapper, router }
}

async function fill(wrapper: Awaited<ReturnType<typeof mountAt>>['wrapper'], code = '123456') {
  await wrapper.find('input[type=email]').setValue('analyst@example.com')
  await wrapper.find('input[type=password]').setValue('a long passphrase')
  await wrapper.find('input[inputmode=numeric]').setValue(code)
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => vi.unstubAllGlobals())

describe('SignInView', () => {
  it('keeps the button off until the code has six digits', async () => {
    const { wrapper } = await mountAt('/sign-in')
    await fill(wrapper, '123')
    expect(wrapper.find('button[type=submit]').attributes('disabled')).toBeDefined()
    await wrapper.find('input[inputmode=numeric]').setValue('123456')
    expect(wrapper.find('button[type=submit]').attributes('disabled')).toBeUndefined()
  })

  it('signs in and goes where the user was heading', async () => {
    const me = { id: 'u1', email: 'a@x', full_name: 'Ada', role: 'analyst', permissions: [] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(me), { status: 200 })))
    const { wrapper, router } = await mountAt('/sign-in?next=/alerts')
    await fill(wrapper)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/alerts')
  })

  it('never redirects off the app after sign-in', async () => {
    const me = { id: 'u1', email: 'a@x', full_name: 'Ada', role: 'analyst', permissions: [] }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(me), { status: 200 })))
    const { wrapper, router } = await mountAt('/sign-in?next=//evil.example')
    await fill(wrapper)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/alerts')
  })

  it('says plainly when the details are wrong and clears the code', async () => {
    const problem = { title: 'Unauthorized', status: 401, code: 'SIGN_IN_FAILED' }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify(problem), { status: 401 })))
    const { wrapper } = await mountAt('/sign-in')
    await fill(wrapper)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[role=alert]').text()).toContain('not right')
    expect((wrapper.find('input[inputmode=numeric]').element as HTMLInputElement).value).toBe('')
  })
})
