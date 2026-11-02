import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import HomeView from '../HomeView.vue'

describe('HomeView', () => {
  it('shows the product heading', () => {
    const wrapper = mount(HomeView)

    expect(wrapper.find('h1').text()).toBe('AML Platform')
  })
})
