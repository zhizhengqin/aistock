import { useState } from 'react'

const SECTIONS: { id: string; title: string; icon: string; content: string[] }[] = [
  {
    "id": "overview",
    "title": "系统介绍",
    "icon": "📋",
    "content": [
      "睿见投研是一套 AI 辅助的 A 股投研分析系统，帮助你从多个维度分析股票、跟踪市场动态、管理持仓风险。",
      "系统共包含 11 个功能模块，下面逐个介绍怎么用。即使你是完全的新手，也可以跟着指南一步步操作。",
      "核心理念：系统收集公开市场数据（行情、财务、新闻等），交给 AI 分析师生成参考报告。所有报告仅供参考，不构成投资建议。"
    ]
  },
  {
    "id": "register",
    "title": "1. 注册与登录",
    "icon": "🔐",
    "content": [
      "注册账户：打开系统页面，点击注册标签，填写用户名、邮箱、密码，点击获取验证码（会发到你的邮箱），输入验证码后点注册。",
      "登录：在登录页输入邮箱或用户名 + 密码，点击登录。登录后刷新页面不会掉线。",
      "忘记密码：点击忘记密码标签，输入注册邮箱和新密码，获取验证码后提交即可重置。",
      "新用户福利：注册后自动赠送 C 档会员 3 天免费试用，可以体验大部分付费功能。"
    ]
  },
  {
    "id": "home",
    "title": "2. 首页行情",
    "icon": "📊",
    "content": [
      "首页展示 A 股市场概览。",
      "大盘指数卡片：页面顶部显示上证指数、沪深300、科创50、深证成指、创业板指的实时涨跌幅，每分钟自动更新，也可手动点刷新。",
      "板块切换：点击下方按钮（银行金融 / 科技互联网 / 新能源 / 大消费 / 高端制造 / 周期资源），切换查看不同板块的走势图和代表个股。",
      "时间切换：K 线图支持 1 月 / 3 月 / 1 年 / 5 年 / 全部几种时间范围。"
    ]
  },
  {
    "id": "analysis",
    "title": "3. 股票分析（核心功能）",
    "icon": "📈",
    "content": [
      "输入一个 A 股代码，AI 会从多个角度帮你分析这只股票。",
      "单股分析：输入 6 位股票代码（如 600519 代表贵州茅台），点击开始分析。5 位 AI 分析师分别从技术面、基本面、资金面、新闻、情绪角度出具报告，最后首席分析师综合出决策卡：评级、目标价、止损价、置信度等。",
      "批量分析：一次输入多只代码（逗号或换行分隔），最多 50 只。",
      "历史记录：可以回看之前做过的所有分析，点击查看看完整报告。",
      "导出 PDF：在报告详情页点击导出 PDF 按钮，下载完整报告。",
      "注意：每份报告底部都有免责声明——本分析仅供参考，不构成任何投资建议。"
    ]
  },
  {
    "id": "main-force",
    "title": "4. 主力选股",
    "icon": "💪",
    "content": [
      "帮你从全市场中筛选出主力资金正在关注的股票。",
      "点击开始选股，系统自动执行选股流水线：先按主力资金净流入取约 40 只候选股 → 策略规则筛选 → 5 位 AI 分析师分析 → 最终精选 3-5 只推荐。",
      "过程中可以看到各阶段数量漏斗。每只推荐股附有入选和排除理由。",
      "名词解释：主力资金 = 大机构（基金、券商、外资）买卖股票的资金，主力流入通常意味着机构看好。"
    ]
  },
  {
    "id": "sector",
    "title": "5. 智策板块",
    "icon": "🌐",
    "content": [
      "每天自动生成板块分析报告。",
      "4 位 AI 分析师分别从宏观策略、板块诊断、资金流向、市场情绪角度分析。",
      "还会自动产出看多/看空列表，每条附置信度（满分 10 分）、逻辑和风险提示。",
      "报告中包含操作节奏建议、风险触发条件和核心跟踪指标。"
    ]
  },
  {
    "id": "dragon-tiger",
    "title": "6. 智瞰龙虎榜",
    "icon": "🐯",
    "content": [
      "龙虎榜是交易所每天公布的异动股名单，反映游资和机构动向。",
      "选择时间范围（3/5/10/15/20/30 天），点击分析。系统会输出上榜记录数、上榜股票数、总净流入、活跃游资数、信心评分，以及推荐股票 Top10 表。",
      "名词解释：龙虎榜 = 交易所每天公布的涨跌幅或换手率异常的股票名单，能看到哪些游资和机构在买卖。"
    ]
  },
  {
    "id": "portfolio",
    "title": "7. 持仓分析",
    "icon": "💼",
    "content": [
      "帮你管理持仓组合，并用 AI 诊断持仓健康度。",
      "添加持仓：输入股票代码、持股数量、成本价，点击添加。系统自动获取最新价格并计算盈亏。",
      "统计卡片：页面顶部显示持仓数、总成本、总市值、总盈亏、监测中数量。",
      "AI 组合诊断：点击 AI 诊断，系统分析你的整个组合，给出健康评分、风险评估、资产配置点评、分散度检查和编号式投资建议。"
    ]
  },
  {
    "id": "realtime",
    "title": "8. 实时监测",
    "icon": "⏱",
    "content": [
      "帮你盯盘，到价自动提醒。",
      "监测配置：输入股票代码、目标价、止损价、止盈止损百分比，可开启 AI 分析。设置检查间隔（默认 10 分钟）。",
      "消息通知：价格触及条件时，系统在消息通知标签生成提醒，可标记为已处理。",
      "AI 交易计划：展示 AI 为你生成的交易建议（买入/卖出/持有），含建议价格、目标价、止损价、置信度和理由。",
      "AI 决策记录：记录 AI 每次判断的摘要，可回看 AI 的决策过程。"
    ]
  },
  {
    "id": "risk",
    "title": "9. 风险预警",
    "icon": "⚠",
    "content": [
      "帮你发现持仓中的风险隐患。",
      "个股风险分析：输入股票代码和分析天数（1-365 天），系统分析风险等级。",
      "投资组合风险：系统自动扫描全部持仓，输出预警总数、最高等级、综合风险评分、分级统计和预警明细（波动率异常、RSI 超买超卖、近期高位回调等）。",
      "全市场活跃预警：管理员专属功能。"
    ]
  },
  {
    "id": "news",
    "title": "10. 实时新闻",
    "icon": "📰",
    "content": [
      "聚合多个财经媒体的实时新闻。",
      "可按时间筛选（6 小时 / 24 小时 / 3 天 / 全部）和按来源筛选（财联社、新浪财经、同花顺、雪球等）。",
      "重要新闻会有 AI 标注：利好/利空、相关行业，帮你快速判断影响。"
    ]
  },
  {
    "id": "us-research",
    "title": "11. 美股隔夜研报",
    "icon": "🇺🇸",
    "content": [
      "每天美股收盘后自动生成研报，帮你判断对 A 股的影响。",
      "四卡片概览：美股情绪、A 股影响方向、风险等级、关注方向。",
      "详细内容：三大指数涨跌、核心美股个股及 A 股映射方向、涨跌幅榜、美债收益率、重要新闻。",
      "可点击重新生成按钮刷新数据。"
    ]
  },
  {
    "id": "membership",
    "title": "12. 会员中心",
    "icon": "💎",
    "content": [
      "系统提供 5 个会员档次，功能配额不同：",
      "免费：每日 1 次股票分析",
      "D 档（88 元/月）：每日 5 次",
      "C 档（128 元/月）：每日 8 次 + 板块 + 龙虎榜",
      "B 档（268 元/月）：每日 20 次 + 持仓 + 盯盘 + 风险预警",
      "A 档（588 元/月）：全功能不限次",
      "会员中心页面显示当前等级、到期时间和今日用量。点击开通后联系客服确认即可。",
      "名词解释：配额 = 每天可使用的次数限制，每天 0 点重置。"
    ]
  },
  {
    "id": "faq",
    "title": "常见问题",
    "icon": "❓",
    "content": [
      "Q：分析报告准确吗？\nA：报告由 AI 根据公开数据生成，仅供参考，不构成投资建议。投资决策请自行判断。",
      "Q：为什么提示今日次数已用完？\nA：你的会员等级每日可用次数有限，升级会员获得更多次数，或等次日 0 点重置。",
      "Q：注册时收不到验证码？\nA：检查邮箱垃圾邮件夹；验证码 5 分钟内有效，过期需重新获取。",
      "Q：手机上能用吗？\nA：可以，系统已适配手机浏览器，所有功能均可正常使用。",
      "Q：定时任务什么时候更新？\nA：板块分析每交易日上午 9:30；龙虎榜每交易日 17:05；新闻每 15 分钟；美股研报每个美股交易日后凌晨。"
    ]
  }
]

export default function Guide() {
  const [active, setActive] = useState('overview')
  const current = SECTIONS.find((s) => s.id === active) || SECTIONS[0]

  return (
    <div className="guide-layout">
      {/* 左栏:章节导航 */}
      <div className="card guide-nav" style={{ padding: 16 }}>
        <div className="section-label" style={{ margin: '0 0 8px' }}>目录</div>
        {SECTIONS.map((s) => (
          <a key={s.id}
            className={`${active === s.id ? 'active' : ''}${s.id === 'faq' ? ' guide-gap' : ''}`}
            style={{ cursor: 'pointer' }}
            onClick={() => setActive(s.id)}>
            {s.title}
          </a>
        ))}
      </div>

      {/* 右栏:章节内容 */}
      <div>
        {/* 移动端章节选择 */}
        <div className="sm:hidden mb-4">
          <select className="select" style={{ width: '100%' }} value={active} onChange={(e) => setActive(e.target.value)}>
            {SECTIONS.map((s) => (
              <option key={s.id} value={s.id}>{s.title}</option>
            ))}
          </select>
        </div>

        <section className="card guide-section">
          <h2 className="card-title">{current.title}</h2>
          {current.content.map((line, i) => {
            const parts = line.split(':')
            if (parts.length >= 2 && parts[0].length <= 8 && !line.includes('\n')) {
              return (
                <p key={i}>
                  <strong>{parts[0]}:</strong>
                  {parts.slice(1).join(':')}
                </p>
              )
            }
            return <p key={i} style={{ whiteSpace: 'pre-line' }}>{line}</p>
          })}
        </section>

        {/* 底部翻页 */}
        <div className="between guide-pager mt16">
          {(() => {
            const idx = SECTIONS.findIndex((s) => s.id === active)
            const prev = idx > 0 ? SECTIONS[idx - 1] : null
            const next = idx < SECTIONS.length - 1 ? SECTIONS[idx + 1] : null
            return (
              <>
                {prev ? (
                  <a className="btn btn-ghost" style={{ cursor: 'pointer' }} onClick={() => setActive(prev.id)}>← {prev.title}</a>
                ) : <span />}
                {next ? (
                  <a className="btn btn-ghost" style={{ cursor: 'pointer' }} onClick={() => setActive(next.id)}>{next.title} →</a>
                ) : <span />}
              </>
            )
          })()}
        </div>
      </div>
    </div>
  )
}
