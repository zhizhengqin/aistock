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

const TIER_BADGE: Record<string, string> = {
  free: 'bg-gray-100 text-gray-600',
  D: 'bg-blue-50 text-blue-600',
  C: 'bg-brand-50 text-brand-600',
  B: 'bg-amber-50 text-amber-600',
  A: 'bg-purple-50 text-purple-600',
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

  if (loading) return <div className="text-gray-400 text-sm py-20 text-center">加载中…</div>

  const currentPlan = plans.find((p) => p.code === me?.tier)

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Current membership card */}
      <div className="bg-white rounded-lg border border-gray-200 p-6">
        <div className="flex items-center justify-between flex-wrap gap-4">
          <div className="flex items-center gap-3">
            <span className={`text-sm px-3 py-1 rounded font-medium ${TIER_BADGE[me?.tier ?? 'free']}`}>
              {currentPlan?.name ?? me?.tier}
            </span>
            {me?.is_trial && (
              <span className="text-xs px-2 py-0.5 rounded bg-green-50 text-green-600 border border-green-200">
                新用户试用{me.days_left !== null ? ` · 剩余 ${me.days_left} 天` : ''}
              </span>
            )}
          </div>
          {me?.tier_expire_at && (
            <span className="text-sm text-gray-500">
              到期时间：{new Date(me.tier_expire_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' })}
            </span>
          )}
        </div>

        {/* Usage meters */}
        <div className="mt-5 grid grid-cols-2 md:grid-cols-4 gap-4">
          {Object.entries(usage).map(([key, u]) => (
            <div key={key} className="border border-gray-100 rounded-lg p-3">
              <div className="flex items-center justify-between">
                <span className="text-xs text-gray-500">{u.name}</span>
                <span className="text-xs text-gray-400">
                  {u.limit === -1 ? '不限' : u.limit === 0 ? '未开通' : `${u.used}/${u.limit}`}
                </span>
              </div>
              <div className="mt-2 h-1.5 rounded-full bg-gray-100 overflow-hidden">
                <div
                  className={`h-full rounded-full ${u.limit !== -1 && u.remaining === 0 ? 'bg-red-400' : 'bg-brand-500'}`}
                  style={{
                    width: u.limit === -1 ? (u.used > 0 ? '100%' : '0%')
                      : u.limit === 0 ? '0%'
                      : `${Math.min(100, (u.used / u.limit) * 100)}%`,
                  }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Plan comparison table */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="px-6 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800">套餐权益对照</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-600">
                <th className="text-left px-6 py-3 font-medium">功能</th>
                {plans.map((p) => (
                  <th key={p.code} className="px-4 py-3 font-medium text-center min-w-28">
                    <div>{p.name}</div>
                    <div className="text-xs font-normal text-gray-400 mt-0.5">{priceText(p)}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(features).map(([key, name]) => (
                <tr key={key} className="border-t border-gray-100">
                  <td className="px-6 py-2.5 text-gray-700">{name}</td>
                  {plans.map((p) => {
                    const v = p.quotas[key] ?? 0
                    return (
                      <td key={p.code} className={`px-4 py-2.5 text-center ${v === 0 ? 'text-gray-300' : v === -1 ? 'text-brand-600 font-medium' : 'text-gray-600'}`}>
                        {quotaText(v)}
                      </td>
                    )
                  })}
                </tr>
              ))}
              <tr className="border-t border-gray-100 bg-gray-50">
                <td className="px-6 py-3 text-gray-500">操作</td>
                {plans.map((p) => (
                  <td key={p.code} className="px-4 py-3 text-center">
                    {p.code === me?.tier ? (
                      <span className="text-xs text-gray-400">当前套餐</span>
                    ) : p.code === 'free' ? (
                      <span className="text-xs text-gray-300">—</span>
                    ) : (
                      <button
                        onClick={() => setContactPlan(p)}
                        className="text-xs px-3 py-1.5 rounded bg-brand-600 text-white hover:bg-brand-700 transition-colors"
                      >
                        开通
                      </button>
                    )}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <p className="text-xs text-gray-400 text-center">
        本系统为 AI 数据分析工具，所有内容仅供参考，不构成投资建议。市场有风险，投资需谨慎。
      </p>

      {/* Contact modal (payment placeholder) */}
      {contactPlan && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setContactPlan(null)}>
          <div className="bg-white rounded-lg shadow-xl p-6 w-96" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-base font-semibold text-gray-800">开通{contactPlan.name}</h3>
            <p className="mt-3 text-sm text-gray-600 leading-relaxed">
              线上支付即将上线。当前请添加客服微信，发送「开通{contactPlan.name} + 您的用户名」，客服确认后即刻为您开通。
            </p>
            <div className="mt-4 p-3 rounded bg-gray-50 text-sm text-gray-500 text-center">
              客服微信：请在左侧栏底部扫码添加
            </div>
            <button
              onClick={() => setContactPlan(null)}
              className="mt-5 w-full py-2 rounded bg-brand-600 text-white text-sm hover:bg-brand-700 transition-colors"
            >
              我知道了
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
