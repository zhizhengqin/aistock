import { useState, useEffect } from 'react'
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'
import { BookOpen, Blocks, BriefcaseBusiness, Crown, Globe2, Home, ListTree, LucideIcon, Newspaper, Radar, ScanSearch, Search, Settings, ShieldAlert } from 'lucide-react'

const NAV_ITEMS = [
  { path: '/', label: '首页行情', icon: Home },
  { path: '/analysis', label: '股票分析', icon: Search },
  { path: '/main-force', label: '主力选股', icon: ScanSearch },
  { path: '/sector', label: '智策板块', icon: Blocks },
  { path: '/dragon-tiger', label: '智瞰龙虎榜', icon: ListTree },
  { path: '/portfolio', label: '持仓分析', icon: BriefcaseBusiness },
  { path: '/realtime', label: '实时监测', icon: Radar },
  { path: '/risk-warning', label: '风险预警', icon: ShieldAlert },
  { path: '/news', label: '实时新闻', icon: Newspaper },
  { path: '/us-research', label: '美股研报', icon: Globe2 },
]

const ACCOUNT_ITEMS = [
  { path: '/guide', label: '使用指南', icon: BookOpen },
  { path: '/membership', label: '会员中心', icon: Crown },
]

const PAGE_TITLES: Record<string, string> = Object.fromEntries(
  [...NAV_ITEMS, ...ACCOUNT_ITEMS, { path: '/admin', label: '系统配置' }].map((i) => [i.path, i.label]),
)

interface UpgradeDetail {
  code: string
  feature: string
  tier: string
  message: string
}

export default function Layout() {
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const location = useLocation()
  const [upgrade, setUpgrade] = useState<UpgradeDetail | null>(null)
  const [isNavOpen, setIsNavOpen] = useState(false)
  const [isMobileNav, setIsMobileNav] = useState(() => window.matchMedia('(max-width: 900px)').matches)

  const closeNav = () => setIsNavOpen(false)

  useEffect(() => {
    const handler = (e: Event) => setUpgrade((e as CustomEvent).detail)
    window.addEventListener('membership:upgrade', handler)
    return () => window.removeEventListener('membership:upgrade', handler)
  }, [])

  useEffect(() => {
    closeNav()
  }, [location.pathname])

  useEffect(() => {
    if (!isNavOpen) return
    const previousOverflow = document.body.style.overflow
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeNav()
    }
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)
    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [isNavOpen])

  useEffect(() => {
    const mobileQuery = window.matchMedia('(max-width: 900px)')
    const onBreakpointChange = (event: MediaQueryListEvent) => {
      setIsMobileNav(event.matches)
      if (!event.matches) closeNav()
    }
    mobileQuery.addEventListener('change', onBreakpointChange)
    return () => mobileQuery.removeEventListener('change', onBreakpointChange)
  }, [])

  const title = PAGE_TITLES[location.pathname] ?? '睿见投研'

  const navLink = (item: { path: string; label: string; icon: LucideIcon }) => {
    const Icon = item.icon
    return (
    <NavLink
      key={item.path}
      to={item.path}
      end={item.path === '/'}
      className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
      onClick={closeNav}
    >
      <Icon className="nav-icon" aria-hidden="true" strokeWidth={1.8} />
      <span>{item.label}</span>
    </NavLink>
    )
  }

  return (
    <div className="shell">
      <button
        type="button"
        className={`nav-backdrop${isNavOpen ? ' open' : ''}`}
        aria-label="关闭菜单遮罩"
        aria-hidden={!isNavOpen}
        tabIndex={isNavOpen ? 0 : -1}
        onClick={closeNav}
      />

      <aside
        id="app-navigation"
        className={`sidebar${isNavOpen ? ' open' : ''}`}
        role={isMobileNav ? 'dialog' : undefined}
        aria-label="主导航"
        aria-modal={isMobileNav ? true : undefined}
        aria-hidden={isMobileNav ? !isNavOpen : undefined}
      >
        <div className="sidebar-logo">
          <div className="sidebar-brand">
            <span className="name">睿见投研</span>
            <span className="sub">AI 投研</span>
          </div>
          <button type="button" className="sidebar-close" aria-label="关闭导航菜单" onClick={closeNav}>×</button>
        </div>
        <nav className="nav">
          {NAV_ITEMS.map(navLink)}
          <div className="nav-section">账户</div>
          {ACCOUNT_ITEMS.map(navLink)}
          {user?.role === 'admin' && navLink({ path: '/admin', label: '系统配置', icon: Settings })}
        </nav>
        {user && (
          <div className="mobile-user-cluster" data-testid="mobile-user-cluster">
            <div className="mobile-user-meta">
              <span className="badge accent">{user.tier} 档会员</span>
              <span className="fg2">{user.username}</span>
            </div>
            <button className="btn-text" onClick={logout}>退出</button>
          </div>
        )}
        <div className="sidebar-foot">客服微信:扫码联系</div>
      </aside>

      {/* 主区 */}
      <div className="main">
        <header className="topbar">
          <button
            type="button"
            className="menu-toggle"
            aria-label="打开导航菜单"
            aria-controls="app-navigation"
            aria-expanded={isNavOpen}
            onClick={() => setIsNavOpen(true)}
          >
            <span aria-hidden="true">☰</span>
          </button>
          <span className="page-title">{title}</span>
          <div className="user-cluster desktop-user-cluster">
            {user && (
              <>
                <span className="badge accent">{user.tier} 档会员</span>
                <span className="fg2">{user.username}</span>
                <button className="btn-text" onClick={logout}>退出</button>
              </>
            )}
          </div>
        </header>

        <main className="content">
          <Outlet />
        </main>
      </div>

      {upgrade && (
        <div className="modal-mask" onClick={() => setUpgrade(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3>{upgrade.code === 'quota_exceeded' ? '今日次数已用完' : '该功能需要升级'}</h3>
            <p className="small fg2" style={{ lineHeight: 1.6 }}>{upgrade.message}</p>
            <div className="flex mt24">
              <button className="btn btn-ghost" style={{ flex: 1 }} onClick={() => setUpgrade(null)}>知道了</button>
              <button
                className="btn btn-primary"
                style={{ flex: 1 }}
                onClick={() => { setUpgrade(null); navigate('/membership') }}
              >
                查看套餐
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
