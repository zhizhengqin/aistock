import { useState } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
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
]

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const { user, logout } = useAuthStore()

  return (
    <div className="flex h-screen bg-gray-50">
      {/* Sidebar */}
      <aside className={`${collapsed ? 'w-16' : 'w-56'} bg-slate-800 text-white transition-all duration-200 flex flex-col`}>
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
        </nav>
        <div className="p-4 border-t border-slate-700 text-xs text-gray-400">
          {!collapsed && <span>客服微信: 扫码联系</span>}
        </div>
      </aside>

      {/* Main area */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Topbar */}
        <header className="flex items-center justify-between h-14 px-6 bg-white border-b border-gray-200">
          <span className="text-lg font-semibold text-gray-800">睿见投研</span>
          <div className="flex items-center gap-4">
            {user && (
              <>
                <span className="text-sm px-2 py-0.5 rounded bg-brand-50 text-brand-600">
                  {user.tier}会员
                </span>
                <span className="text-sm text-gray-600">{user.username}</span>
                <button onClick={logout} className="text-sm text-gray-500 hover:text-red-500">
                  退出
                </button>
              </>
            )}
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
