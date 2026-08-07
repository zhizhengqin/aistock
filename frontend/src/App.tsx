import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/auth'
import Layout from './components/Layout'
import Login from './pages/Login'
import Home from './pages/Home'
import Placeholder from './pages/Placeholder'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { user, loaded } = useAuthStore()
  useEffect(() => {
    useAuthStore.getState().fetchUser()
  }, [])
  if (!loaded) return <div className="flex items-center justify-center h-screen text-gray-400">加载中...</div>
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/" element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route index element={<Home />} />
          <Route path="analysis" element={<Placeholder title="股票分析" />} />
          <Route path="main-force" element={<Placeholder title="主力选股" />} />
          <Route path="sector" element={<Placeholder title="智策板块" />} />
          <Route path="dragon-tiger" element={<Placeholder title="智瞰龙虎榜" />} />
          <Route path="portfolio" element={<Placeholder title="持仓分析" />} />
          <Route path="realtime" element={<Placeholder title="实时监测" />} />
          <Route path="risk-warning" element={<Placeholder title="风险预警" />} />
          <Route path="news" element={<Placeholder title="实时新闻" />} />
          <Route path="us-research" element={<Placeholder title="美股研报" />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
