import { useState, useEffect } from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { useAuthStore } from '../stores/auth'

const NAV_ITEMS = [
  { path: '/', label: '首页', icon: '📊' },
  { path: '/analysis', label: '股票分析', icon: '📈' },
  { path: '/main-force', label: '主力选股', icon: '💪' },
  { path: '/sector', label: '智策板块', icon: '🌐' },
  { path: '/dragon-tiger', label: '智瞰龙虎榜', icon: '🐯' },
  { path: '/portfolio', label: '持仓分析', icon: '💼' },
  { path: '/realtime', label: '实时监测', icon: '⏱' },
  { path: '/risk-warning', label: '风险预警', icon: '⚠' },
  { path: '/news', label: '实时新闻', icon: '📰' },
  { path: '/us-research', label: '美股研报', icon: '🇺🇸' },
  { path: '/guide', label: '使用指南', icon: '📖' },
  { path: '/membership', label: '会员中心', icon: '💎' },
]

interface UpgradeDetail {
  code: string
  feature: string
  tier: string
  message: string
}

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const { user, logout } = useAuthStore()
  const navigate = useNavigate()
  const [upgrade, setUpgrade] = useState<UpgradeDetail | null>(null)

  useEffect(() => {
    const handler = (e: Event) => setUpgrade((e as CustomEvent).detail)
    window.addEventListener('membership:upgrade', handler)
    return () => window.removeEventListener('membership:upgrade', handler)
  }, [])

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className={`${collapsed ? 'w-16' : 'w-56'} bg-slate-800 text-white transition-all duration-200 flex flex-col max-sm:w-14 max-sm:shrink-0`}>
        <div className="flex items-center justify-between p-4 h-14 border-b border-slate-700">
          {!collapsed && <span className="text-lg font-bold">睿见投研</span>}
          <button onClick={() => setCollapsed(!collapsed)} className="text-gray-400 hover:text-white">
            {collapsed ? '▶' : '◀'}
          </button>
        </div>
        <nav className="flex-1 py-2 overflow-y-auto">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.path}
              to={item.path}
              end={item.path === '/'}
              className={({ isActive }) =>
                `flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                  isActive ? 'bg-brand-600 text-white' : 'text-gray-300 hover:bg-slate-700'
                }`
              }
            >
              <span className="text-base">{item.icon}</span>
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
          {user?.role === 'admin' && (
            <NavLink
              to="/admin"
              className={({ isActive }) =>
                `flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                  isActive ? 'bg-brand-600 text-white' : 'text-gray-300 hover:bg-slate-700'
                }`
              }
            >
              <span className="text-base">⚙️</span>
              {!collapsed && <span>系统配置</span>}
            </NavLink>
          )}
        </nav>
        <div className="p-4 border-t border-slate-700 text-xs text-gray-400">
          {!collapsed && <span>客服微信: 扫码联系</span>}
        </div>
      </aside>

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Topbar */}
        <header className="flex items-center justify-between h-14 px-3 sm:px-6 bg-white border-b border-gray-200">
          <span className="text-lg font-semibold text-gray-800">睿见投研</span>
          <div className="flex items-center gap-2 sm:gap-4">
            {user && (
              <>
                <span className="text-sm px-2 py-0.5 rounded bg-brand-50 text-brand-600">
                  {user.tier}会员
                </span>
                <span className="text-sm text-gray-600 hidden sm:inline">{user.username}</span>
                <button onClick={logout} className="text-sm text-gray-500 hover:text-red-500">
                  退出
                </button>
              </>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-3 sm:p-6">
          <Outlet />
        </main>

        {upgrade && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setUpgrade(null)}>
            <div className="bg-white rounded-lg shadow-xl p-6 w-96" onClick={(e) => e.stopPropagation()}>
              <h3 className="text-base font-semibold text-gray-800">
                {upgrade.code === 'quota_exceeded' ? '今日次数已用完' : '该功能需要升级'}
              </h3>
              <p className="mt-3 text-sm text-gray-600 leading-relaxed">{upgrade.message}</p>
              <div className="mt-5 flex gap-3">
                <button
                  onClick={() => setUpgrade(null)}
                  className="flex-1 py-2 rounded border border-gray-200 text-sm text-gray-600 hover:bg-gray-50"
                >
                  知道了
                </button>
                <button
                  onClick={() => { setUpgrade(null); navigate('/membership') }}
                  className="flex-1 py-2 rounded bg-brand-600 text-white text-sm hover:bg-brand-700"
                >
                  查看套餐
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
