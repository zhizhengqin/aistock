import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const baseURL = process.env.QA_BASE_URL || 'http://127.0.0.1:4175'
const failures = []
let viteProcess

async function waitForServer(url) {
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    try { if ((await fetch(url)).ok) return } catch {}
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 200))
  }
  throw new Error(`E2E server did not become ready: ${url}`)
}

if (!process.env.QA_BASE_URL) {
  viteProcess = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', '4175'], { cwd: frontendDir, stdio: 'inherit' })
  await waitForServer(baseURL)
}

const browser = await chromium.launch({ headless: true })
const envelope = (data) => ({ code: 0, message: 'ok', data })
const report = {
  stock_code: '600519',
  stock_name: '贵州茅台',
  stock_info: { price: 1685.5, change_pct: 1.32, pe_ttm: 22.8, pb: 7.5, market_cap: 20000, industry: '白酒' },
  indicators: { ma: { MA5: 1680, MA20: 1660, MA60: 1600 }, macd: { DIF: 1, DEA: 0.8, MACD: 0.2 }, rsi: { RSI: 65 }, kdj: { K: 70, D: 60, J: 80 }, boll: { UP: 1710, MID: 1650, LOW: 1590 } },
  kline: Array.from({ length: 60 }, (_, index) => ({ date: `2026-06-${String((index % 28) + 1).padStart(2, '0')}`, open: 1600 + index, close: 1605 + index, high: 1610 + index, low: 1595 + index, volume: 10000 + index * 10 })),
  analysts: {
    technical: { trend: '震荡向上', score: 72, pattern: '上升通道', indicator_readings: '均线多头', breakout_prob: 65 },
    fundamental: { financial_health: '稳健', profitability: '良好', valuation: '合理', score: 8, detail: '基本面稳定' },
    capital: { main_flow: '净流入', flow_trend: '改善', score: 7, detail: '资金流入' },
    news: { sentiment_rating: '利好', key_news: ['业绩预增'], impact: '正面' },
    sentiment: { sentiment_score: 65, indicators: 'RSI偏强', assessment: '情绪回暖' },
    risk: { risk_level: '中等风险', risk_score: 42, analysis: '波动可控', advice: '设置止损' },
  },
  decision: { rating: '持有', target_price: 1750, stop_loss: 1580, confidence: 72, entry_range: '1650-1680', take_profit: '1750', holding_period: '1-3个月', position_size: '20%', risk_warning: '关注波动', key_watchpoints: ['业绩'], meeting_summary: '综合分析建议持有' },
  disclaimer: '本分析仅供参考，不构成任何投资建议。',
  analyzed_at: '2026-08-23T00:00:00Z',
}
const snapshot = {
  stock_code: report.stock_code,
  info: { code: '600519.SS', name: report.stock_name, ...report.stock_info },
  indicators: report.indicators,
  kline: report.kline,
  financial: null,
  warnings: [],
}

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  const context = await browser.newContext({ viewport })
  let polls = 0
  await context.addInitScript(() => localStorage.setItem('access_token', 'qa-analysis-token'))
  await context.route((url) => new URL(url).pathname.startsWith('/api/'), async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    let response = envelope([])
    if (path === '/api/auth/me') response = envelope({ id: 1, username: 'qa-analysis', role: 'user', tier: 'A' })
    else if (path === '/api/stocks/analyze' && request.method() === 'POST') response = envelope({ tasks: [{ task_id: 1, stock_code: '600519' }] })
    else if (path === '/api/stocks/600519/snapshot') response = envelope(snapshot)
    else if (path === '/api/tasks/1') {
      polls += 1
      const steps = ['technical', 'fundamental', 'capital', 'news', 'sentiment', 'risk', 'chief'].map((key) => ({ key, label: ({ technical: '技术面分析师', fundamental: '基本面分析师', capital: '资金面分析师', news: '消息面分析师', sentiment: '情绪面分析师', risk: '风险分析师', chief: '首席分析师' })[key], status: key === 'technical' ? 'completed' : 'waiting', result: key === 'technical' ? report.analysts.technical : null, error: null }))
      response = envelope(polls === 1 ? { id: 1, status: 'running', progress: 62, phase: 'analyzing', error: null, result: null, steps } : { id: 1, status: 'success', progress: 100, phase: 'completed', error: null, result: { report_id: 88 }, steps })
    } else if (path === '/api/stocks/user/results/88') response = envelope({ id: 88, report })
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(response) })
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => failures.push(`${viewport.width}px pageerror: ${error.message}`))
  page.on('console', (message) => { if (message.type() === 'error') failures.push(`${viewport.width}px console: ${message.text()}`) })
  await page.goto(`${baseURL}/analysis`, { waitUntil: 'domcontentloaded' })
  await page.getByPlaceholder('输入股票代码，如 600519').fill('600519')
  await page.getByRole('button', { name: '开始分析' }).click()
  await page.getByText('技术面分析师').waitFor()
  await page.getByText('震荡向上').waitFor()
  await page.getByText('贵州茅台').first().waitFor()
  await page.getByText('1685.5').first().waitFor()
  await page.getByText('K线与成交量').first().waitFor()
  await page.getByText('技术指标').first().waitFor()
  await page.getByText('MA5').first().waitFor()
  if (await page.locator('body').textContent().then((text) => text?.includes('"trend"'))) failures.push(`${viewport.width}px raw JSON exposed during partial result`)
  await page.screenshot({ path: resolve('/tmp', `aistock-analysis-${viewport.width}-partial.png`), fullPage: true })
  await page.getByText('AI 投研会议 · 最终决策').waitFor({ timeout: 5000 })
  await page.getByText('K线与成交量').first().waitFor()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  if (overflow !== 0) failures.push(`${viewport.width}px horizontal overflow ${overflow}px`)
  await page.screenshot({ path: resolve('/tmp', `aistock-analysis-${viewport.width}-final.png`), fullPage: true })
  await context.close()
}

await browser.close()
if (viteProcess) viteProcess.kill('SIGTERM')
console.log(JSON.stringify({ failures }, null, 2))
if (failures.length) process.exit(1)
