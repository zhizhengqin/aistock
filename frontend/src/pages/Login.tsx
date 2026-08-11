import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import client from '../api/client'
import { errMsg } from '../utils/errors'
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
      setError(errMsg(err, '操作失败'))
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
    <div className="auth-wrap">
      <div>
        <div className="auth-brand">
          <div className="name">睿见投研</div>
          <div className="sub">AI 辅助 A 股投研系统</div>
        </div>

        <div className="card featured auth-card">
          <div className="tabs">
            {tabs.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={`tab${tab === t.key ? ' active' : ''}`}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="mt24">
            {tab === 'login' && (
              <div className="field">
                <label>邮箱或用户名</label>
                <input
                  className="input"
                  type="text"
                  placeholder="邮箱或用户名"
                  value={form.account}
                  onChange={(e) => update('account', e.target.value)}
                />
              </div>
            )}

            {tab === 'register' && (
              <div className="field">
                <label>用户名</label>
                <input
                  className="input"
                  type="text"
                  placeholder="用户名"
                  value={form.username}
                  onChange={(e) => update('username', e.target.value)}
                />
              </div>
            )}

            {(tab === 'register' || tab === 'forgot') && (
              <>
                <div className="field mt16">
                  <label>邮箱</label>
                  <input
                    className="input"
                    type="email"
                    placeholder="邮箱"
                    value={form.email}
                    onChange={(e) => update('email', e.target.value)}
                  />
                </div>
                <div className="field mt16">
                  <label>验证码</label>
                  <div className="flex" style={{ flexWrap: 'nowrap' }}>
                    <input
                      className="input"
                      style={{ flex: 1 }}
                      type="text"
                      placeholder="验证码"
                      value={form.code}
                      onChange={(e) => update('code', e.target.value)}
                    />
                    <button className="btn btn-ghost" type="button" onClick={sendCode} disabled={countdown > 0}>
                      {countdown > 0 ? `${countdown}s` : '获取验证码'}
                    </button>
                  </div>
                  {tab === 'register' && <span className="caption">新用户注册即赠 C 档会员 3 天免费试用</span>}
                </div>
              </>
            )}

            <div className="field mt16">
              <label>{tab === 'forgot' ? '新密码' : '密码'}</label>
              <input
                className="input"
                type="password"
                placeholder={tab === 'forgot' ? '新密码' : '密码'}
                value={form.password}
                onChange={(e) => update('password', e.target.value)}
              />
            </div>

            {error && <p className="small mt16" style={{ color: 'var(--up)' }}>{error}</p>}

            <button
              className="btn btn-primary mt24"
              style={{ width: '100%' }}
              onClick={handleSubmit}
              disabled={loading}
            >
              {loading ? '处理中...' : tab === 'login' ? '登录' : tab === 'register' ? '注册' : '重置密码'}
            </button>
          </div>
        </div>

        <p className="auth-disclaimer">本系统提供的数据分析仅供参考，不构成投资建议</p>
      </div>
    </div>
  )
}
