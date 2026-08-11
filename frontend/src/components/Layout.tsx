import { useState, useEffect } from 'react'
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'

const NAV_ITEMS = [
  { path: '/', label: '首页行情' },
  { path: '/analysis', label: '股票分析' },
  { path: '/main-force', label: '主力选股' },
  { path: '/sector', label: '智策板块' },
  { path: '/dragon-tiger', label: '智瞰龙虎榜' },
  { path: '/portfolio', label: '持仓分析' },
  { path: '/realtime', label: '实时监测' },
  { path: '/risk-warning', label: '风险预警' },
  { path: '/news', label: '实时新闻' },
  { path: '/us-research', label: '美股研报' },
]

const ACCOUNT_ITEMS = [
  { path: '/guide', label: '使用指南' },
  { path: '/membership', label: '会员中心' },
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

  useEffect(() => {
    const handler = (e: Event) => setUpgrade((e as CustomEvent).detail)
    window.addEventListener('membership:upgrade', handler)
    return () => window.removeEventListener('membership:upgrade', handler)
  }, [])

  const title = PAGE_TITLES[location.pathname] ?? '睿见投研'

  const navLink = (item: { path: string; label: string }) => (
    <NavLink
      key={item.path}
      to={item.path}
      end={item.path === '/'}
      className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
    >
      <span className="dot"></span>
      <span>{item.label}</span>
    </NavLink>
  )

  return (
    <div className="shell">
      {/* 侧栏（Zapier Black 深色区） */}
      <aside className="sidebar">
        <div className="sidebar-logo">
          <span className="name">睿见投研</span>
          <span className="sub">AI 投研</span>
        </div>
        <nav className="nav">
          {NAV_ITEMS.map(navLink)}
          <div className="nav-section">账户</div>
          {ACCOUNT_ITEMS.map(navLink)}
          {user?.role === 'admin' && navLink({ path: '/admin', label: '系统配置' })}
        </nav>
        <div className="sidebar-foot">客服微信:扫码联系</div>
      </aside>

      {/* 主区 */}
      <div className="main">
        <header className="topbar">
          <span className="page-title">{title}</span>
          <div className="user-cluster">
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
