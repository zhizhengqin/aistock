import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Layout from './Layout'
import { useAuthStore } from '../stores/auth'

type TestUser = NonNullable<ReturnType<typeof useAuthStore.getState>['user']>

const regularUser: TestUser = {
  id: 1,
  username: 'test-user',
  email: 'test@example.com',
  role: 'user',
  tier: 'A',
}

let mediaMatches = true
let mediaListener: ((event: MediaQueryListEvent) => void) | undefined

function RouteChanger() {
  const navigate = useNavigate()
  return <button onClick={() => navigate('/news')}>外部切换路由</button>
}

function renderLayout(path = '/', user: TestUser = regularUser) {
  useAuthStore.setState({ user, loaded: true })
  return render(
    <MemoryRouter
      initialEntries={[path]}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route element={<Layout />}>
          <Route path="*" element={<><div>页面内容</div><RouteChanger /></>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  )
}

function drawer() {
  const element = document.querySelector<HTMLElement>('#app-navigation')
  if (!element) throw new Error('navigation drawer is missing')
  return element
}

async function openDrawer() {
  await userEvent.click(screen.getByRole('button', { name: '打开导航菜单' }))
}

describe('Layout mobile navigation', () => {
  beforeEach(() => {
    mediaMatches = true
    mediaListener = undefined
    document.body.style.overflow = ''
    vi.stubGlobal('matchMedia', vi.fn().mockImplementation((query: string) => ({
      matches: mediaMatches,
      media: query,
      onchange: null,
      addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        mediaListener = listener
      },
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
  })

  it('renders the mobile drawer closed initially', () => {
    renderLayout()

    expect(screen.getByRole('button', { name: '打开导航菜单' })).toHaveAttribute('aria-expanded', 'false')
    expect(drawer()).toHaveAttribute('aria-hidden', 'true')
  })

  it('keeps the desktop navigation exposed to assistive technology', () => {
    mediaMatches = false
    renderLayout()

    expect(drawer()).not.toHaveAttribute('aria-hidden')
    expect(drawer()).not.toHaveAttribute('aria-modal')
    expect(drawer()).not.toHaveAttribute('role', 'dialog')
  })

  it('opens the drawer and locks background scrolling', async () => {
    renderLayout()
    await openDrawer()

    expect(drawer()).toHaveAttribute('aria-hidden', 'false')
    expect(screen.getByRole('button', { name: '打开导航菜单' })).toHaveAttribute('aria-expanded', 'true')
    expect(document.body.style.overflow).toBe('hidden')
  })

  it('closes from the close button and restores scrolling', async () => {
    renderLayout()
    await openDrawer()
    await userEvent.click(drawer().querySelector<HTMLButtonElement>('.sidebar-close')!)

    expect(drawer()).toHaveAttribute('aria-hidden', 'true')
    expect(document.body.style.overflow).toBe('')
  })

  it('closes from the backdrop', async () => {
    renderLayout()
    await openDrawer()
    await userEvent.click(document.querySelector<HTMLButtonElement>('.nav-backdrop')!)

    expect(drawer()).toHaveAttribute('aria-hidden', 'true')
  })

  it('closes when Escape is pressed', async () => {
    renderLayout()
    await openDrawer()
    await userEvent.keyboard('{Escape}')

    expect(drawer()).toHaveAttribute('aria-hidden', 'true')
  })

  it('navigates from a menu item and closes the drawer', async () => {
    renderLayout()
    await openDrawer()
    await userEvent.click(screen.getByRole('link', { name: '股票分析' }))

    expect(screen.getByText('股票分析', { selector: '.page-title' })).toBeInTheDocument()
    expect(drawer()).toHaveAttribute('aria-hidden', 'true')
  })

  it('closes when the route changes outside the drawer', async () => {
    renderLayout()
    await openDrawer()
    await userEvent.click(screen.getByRole('button', { name: '外部切换路由' }))

    expect(screen.getByText('实时新闻', { selector: '.page-title' })).toBeInTheDocument()
    expect(drawer()).toHaveAttribute('aria-hidden', 'true')
  })

  it('shows system configuration only to administrators', () => {
    const { unmount } = renderLayout()
    expect(drawer().querySelector('a[href="/admin"]')).not.toBeInTheDocument()

    unmount()
    renderLayout('/', { ...regularUser, role: 'admin' })
    expect(drawer().querySelector('a[href="/admin"]')).toHaveTextContent('系统配置')
  })

  it('shows membership, username, and logout in the mobile user area', () => {
    renderLayout()
    const mobileUser = screen.getByTestId('mobile-user-cluster')

    expect(mobileUser).toHaveTextContent('A 档会员')
    expect(mobileUser).toHaveTextContent('test-user')
    expect(mobileUser.querySelector('button')).toHaveTextContent('退出')
  })

  it('restores the previous body overflow value when unmounted', async () => {
    document.body.style.overflow = 'clip'
    const { unmount } = renderLayout()
    await openDrawer()
    expect(document.body.style.overflow).toBe('hidden')

    unmount()
    expect(document.body.style.overflow).toBe('clip')
  })

  it('closes and restores scrolling when the viewport crosses to desktop', async () => {
    renderLayout()
    await openDrawer()

    mediaMatches = false
    act(() => mediaListener?.({ matches: false } as MediaQueryListEvent))

    expect(drawer()).not.toHaveClass('open')
    expect(drawer()).not.toHaveAttribute('aria-hidden')
    expect(document.body.style.overflow).toBe('')
  })
})
