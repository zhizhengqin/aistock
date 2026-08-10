import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useAuthStore } from './stores/auth'
import Layout from './components/Layout'
import Login from './pages/Login'
import Home from './pages/Home'
import Analysis from './pages/Analysis'
import MainForce from './pages/MainForce'
import Sector from './pages/Sector'
import DragonTiger from './pages/DragonTiger'
import Portfolio from './pages/Portfolio'
import Realtime from './pages/Realtime'
import RiskWarning from './pages/RiskWarning'
import News from './pages/News'
import USResearch from './pages/USResearch'
import Membership from './pages/Membership'
import Guide from './pages/Guide'

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
          <Route path="analysis" element={<Analysis />} />
          <Route path="main-force" element={<MainForce />} />
          <Route path="sector" element={<Sector />} />
          <Route path="dragon-tiger" element={<DragonTiger />} />
          <Route path="portfolio" element={<Portfolio />} />
          <Route path="realtime" element={<Realtime />} />
          <Route path="risk-warning" element={<RiskWarning />} />
          <Route path="news" element={<News />} />
          <Route path="us-research" element={<USResearch />} />
          <Route path="guide" element={<Guide />} />
          <Route path="membership" element={<Membership />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
