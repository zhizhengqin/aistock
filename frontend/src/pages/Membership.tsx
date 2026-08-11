import { useState, useEffect, useCallback } from 'react'
import client from '../api/client'

interface Plan {
  code: string
  name: string
  price_monthly_cents: number
  price_yearly_cents: number
  quotas: Record<string, number>
  sort_order: number
}

interface MeInfo {
  tier: string
  raw_tier: string
  tier_expire_at: string | null
  is_trial: boolean
  days_left: number | null
}

interface UsageEntry {
  name: string
  used: number
  limit: number
  remaining: number | null
}

function quotaText(v: number): string {
  if (v === -1) return '不限'
  if (v === 0) return '—'
  return `${v}次/日`
}

function priceText(plan: Plan): string {
  const parts: string[] = []
  if (plan.price_monthly_cents > 0) parts.push(`¥${plan.price_monthly_cents / 100}/月`)
  if (plan.price_yearly_cents > 0) parts.push(`¥${plan.price_yearly_cents / 100}/年`)
  return parts.length ? parts.join(' · ') : '免费'
}

export default function Membership() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [features, setFeatures] = useState<Record<string, string>>({})
  const [me, setMe] = useState<MeInfo | null>(null)
  const [usage, setUsage] = useState<Record<string, UsageEntry>>({})
  const [loading, setLoading] = useState(true)
  const [contactPlan, setContactPlan] = useState<Plan | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [plansResp, meResp, usageResp] = await Promise.all([
        client.get('/membership/plans'),
        client.get('/membership/me'),
        client.get('/membership/usage'),
      ])
      setPlans(plansResp.data.data.plans)
      setFeatures(plansResp.data.data.features)
      setMe(meResp.data.data)
      setUsage(usageResp.data.data)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  if (loading) return <div className="empty">加载中…</div>

  const currentPlan = plans.find((p) => p.code === me?.tier)

  return (
    <>
      <section className="card card-accent">
        <div className="between wrap">
          <div className="flex wrap">
            <h2 className="card-title" style={{ margin: 0 }}>当前会员</h2>
            <span className="badge accent">{currentPlan?.name ?? me?.tier}</span>
            {me?.is_trial && (
              <span className="badge down">新用户试用{me.days_left !== null ? ` · 剩余 ${me.days_left} 天` : ''}</span>
            )}
          </div>
          {me?.tier_expire_at && (
            <span className="caption">
              到期时间:{new Date(me.tier_expire_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })}
            </span>
          )}
        </div>
        <p className="caption mt16" style={{ marginBottom: 0 }}>
          {me?.is_trial
            ? '试用期内可完整体验当前档位权益；试用结束后将自动转为免费档，可在下表选择套餐开通。'
            : '档位到期后自动转为免费档，历史报告保留可查。'}
        </p>
      </section>

      <section>
        <div className="section-label">今日用量</div>
        <div className="grid4">
          {Object.entries(usage).map(([key, u]) => (
            <div key={key} className={`kpi${u.limit === 0 ? ' usage-off' : ''}`}>
              <div className="k-label">{u.name}</div>
              {u.limit === -1 ? (
                <div className="k-value" style={{ fontSize: 24 }}>不限</div>
              ) : u.limit === 0 ? (
                <div className="k-value muted" style={{ fontSize: 24 }}>未开通</div>
              ) : (
                <>
                  <div className="k-value mono" style={{ fontSize: 24 }}>{u.used} / {u.limit} <span className="caption">次</span></div>
                  <div className="progress mt8"><i style={{ width: `${Math.min(100, (u.used / u.limit) * 100)}%` }} /></div>
                </>
              )}
              <div className="caption mt8">
                {u.limit === 0 ? '更高档位会员可用' : u.limit === -1 ? '当前档位不限次' : u.remaining === 0 ? '今日额度已用完' : '每日 0 点重置'}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card">
        <h2 className="card-title">套餐权益对照</h2>
        <table className="table">
          <thead>
            <tr>
              <th>功能</th>
              {plans.map((p) => (
                <th key={p.code} className={p.code === me?.tier ? 'tier-cur' : ''}>
                  {p.name} {priceText(p)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(features).map(([key, name]) => (
              <tr key={key}>
                <td>{name}</td>
                {plans.map((p) => {
                  const v = p.quotas[key] ?? 0
                  return (
                    <td key={p.code} className={`${p.code === me?.tier ? 'tier-cur ' : ''}${v === 0 ? 'dash' : 'check'}`}>
                      {quotaText(v)}
                    </td>
                  )
                })}
              </tr>
            ))}
            <tr>
              <td>操作</td>
              {plans.map((p) => (
                <td key={p.code} className={p.code === me?.tier ? 'tier-cur' : ''}>
                  {p.code === me?.tier ? (
                    <span className="badge accent">当前套餐</span>
                  ) : p.code === 'free' ? (
                    <span className="dash">—</span>
                  ) : (
                    <button className="btn btn-ghost" onClick={() => setContactPlan(p)}>开通</button>
                  )}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
        <p className="caption mt16">所有档位均包含首页行情、主力选股、实时新闻与美股隔夜研报。档位到期后自动转为免费档，历史报告保留可查。</p>
      </section>

      <p className="disclaimer">本系统为 AI 数据分析工具，所有内容仅供参考，不构成投资建议。市场有风险，投资需谨慎。</p>

      {contactPlan && (
        <div className="modal-mask" onClick={() => setContactPlan(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h3 className="card-title-sm" style={{ margin: 0 }}>开通{contactPlan.name}</h3>
            <p className="small fg2 mt16" style={{ margin: 0, lineHeight: 1.7 }}>
              线上支付即将上线。当前请添加客服微信，发送「开通{contactPlan.name} + 您的用户名」，客服确认后即刻为您开通。
            </p>
            <div className="modal-note">客服微信：请在左侧栏底部扫码添加</div>
            <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => setContactPlan(null)}>我知道了</button>
          </div>
        </div>
      )}
    </>
  )
}
