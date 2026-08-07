import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import client from '../api/client'
import { useAuthStore } from '../stores/auth'

type Tab = 'login' | 'register' | 'forgot'

export default function Login() {
  const [tab, setTab] = useState<Tab>('login')
  const [form, setForm] = useState({
    account: '',
    username: '',
    email: '',
    password: '',
    confirmPassword: '',
    code: '',
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [countdown, setCountdown] = useState(0)
  const navigate = useNavigate()
  const setAuth = useAuthStore((s) => s.setAuth)

  useEffect(() => {
    if (countdown <= 0) return
    const t = setTimeout(() => setCountdown(countdown - 1), 1000)
    return () => clearTimeout(t)
  }, [countdown])

  const update = (k: string, v: string) => setForm({ ...form, [k]: v })

  const sendCode = async () => {
    if (!form.email) {
      setError('请输入邮箱')
      return
    }
    try {
      const endpoint = tab === 'forgot' ? '/auth/forgot-password' : '/auth/send-verification-code'
      await client.post(endpoint, { email: form.email })
      setCountdown(60)
      setError('')
    } catch {
      setError('验证码发送失败')
    }
  }

  const handleSubmit = async () => {
    setError('')
    setLoading(true)
    try {
      if (tab === 'login') {
        const resp = await client.post('/auth/login', {
          account: form.account,
          password: form.password,
        })
        setAuth(resp.data.data)
        navigate('/')
      } else if (tab === 'register') {
        const resp = await client.post('/auth/register', {
          username: form.username,
          email: form.email,
          password: form.password,
          code: form.code,
        })
        setAuth(resp.data.data)
        navigate('/')
      } else {
        await client.post('/auth/reset-password', {
          email: form.email,
          code: form.code,
          new_password: form.password,
        })
        setError('密码重置成功，请登录')
        setTab('login')
      }
    } catch (err: any) {
      setError(err.response?.data?.detail || '操作失败')
    } finally {
      setLoading(false)
    }
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: 'login', label: '登录' },
    { key: 'register', label: '注册' },
    { key: 'forgot', label: '忘记密码' },
  ]

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-100 to-blue-50">
      <div className="w-full max-w-md bg-white rounded-lg shadow-lg p-8">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-gray-800">睿见投研</h1>
          <p className="text-sm text-gray-500 mt-1">AI 辅助 A 股投研系统</p>
        </div>

        <div className="flex gap-2 mb-6 border-b border-gray-200">
          {tabs.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`flex-1 pb-2 text-sm font-medium transition-colors ${
                tab === t.key
                  ? 'text-brand-600 border-b-2 border-brand-600'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        <div className="space-y-4">
          {tab === 'login' && (
            <input
              type="text"
              placeholder="邮箱或用户名"
              value={form.account}
              onChange={(e) => update('account', e.target.value)}
              className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:outline-none focus:border-brand-500"
            />
          )}

          {tab === 'register' && (
            <input
              type="text"
              placeholder="用户名"
              value={form.username}
              onChange={(e) => update('username', e.target.value)}
              className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:outline-none focus:border-brand-500"
            />
          )}

          {(tab === 'register' || tab === 'forgot') && (
            <>
              <input
                type="email"
                placeholder="邮箱"
                value={form.email}
                onChange={(e) => update('email', e.target.value)}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:outline-none focus:border-brand-500"
              />
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder="验证码"
                  value={form.code}
                  onChange={(e) => update('code', e.target.value)}
                  className="flex-1 px-4 py-2.5 border border-gray-300 rounded-lg focus:outline-none focus:border-brand-500"
                />
                <button
                  onClick={sendCode}
                  disabled={countdown > 0}
                  className="px-4 py-2.5 text-sm text-brand-600 border border-brand-500 rounded-lg disabled:text-gray-400 disabled:border-gray-300"
                >
                  {countdown > 0 ? `${countdown}s` : '获取验证码'}
                </button>
              </div>
            </>
          )}

          <input
            type="password"
            placeholder={tab === 'forgot' ? '新密码' : '密码'}
            value={form.password}
            onChange={(e) => update('password', e.target.value)}
            className="w-full px-4 py-2.5 border border-gray-300 rounded-lg focus:outline-none focus:border-brand-500"
          />

          {error && <p className="text-sm text-red-500">{error}</p>}

          <button
            onClick={handleSubmit}
            disabled={loading}
            className="w-full py-2.5 bg-brand-600 text-white rounded-lg font-medium hover:bg-brand-700 disabled:opacity-50"
          >
            {loading ? '处理中...' : tab === 'login' ? '登录' : tab === 'register' ? '注册' : '重置密码'}
          </button>
        </div>

        <p className="mt-6 text-xs text-gray-400 text-center">
          本系统提供的数据分析仅供参考，不构成投资建议
        </p>
      </div>
    </div>
  )
}
