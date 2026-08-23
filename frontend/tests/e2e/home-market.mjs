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
const envelope = (data, meta = {}) => ({ code: 0, message: 'ok', data, meta })
const meta = { provider: '东方财富', freshness: 'fresh', data_at: '2026-08-22T07:30:00Z', fetched_at: '2026-08-22T07:30:01Z', trade_date: '2026-08-22' }

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  const context = await browser.newContext({ viewport })
  await context.addInitScript(() => localStorage.setItem('access_token', 'qa-home-token'))
  await context.route((url) => new URL(url).pathname.startsWith('/api/'), async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === '/api/auth/me') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ id: 1, username: 'qa-home', role: 'user', tier: 'A' })) })
    if (path === '/api/stocks/market-indices') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope([{ code: '000001.SS', name: '上证指数', price: 3200, change_pct: 0.8 }], meta)) })
    if (path === '/api/stocks/market-hotspots') {
      const kind = url.searchParams.get('kind') || 'industry'
      const items = kind === 'theme'
        ? [{ board_code: 'BK0002', board_name: '新能源', kind, change_pct: 2.1, hot_score: 91.2, rank: 1, trend_status: 'heating', streak_days: 3, trade_date: meta.trade_date }]
        : [{ board_code: 'BK0001', board_name: '银行', kind, change_pct: 1.2, hot_score: 86.4, rank: 1, trend_status: 'steady', streak_days: 1, trade_date: meta.trade_date }, { board_code: 'BK0003', board_name: '电子', kind, change_pct: -0.4, hot_score: 60.4, rank: 2, trend_status: 'cooling', streak_days: 2, trade_date: meta.trade_date }]
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ kind, items }, meta)) })
    }
    if (path === '/api/stocks/market-cloud') {
      const kind = url.searchParams.get('kind') || 'industry'
      const nodes = [{ code: 'BK0001', name: '银行', kind, value: 100, market_cap: 100, change_pct: 1.2, trade_date: meta.trade_date }, { code: 'BK0003', name: '电子', kind, value: 80, market_cap: 80, change_pct: -0.4, trade_date: meta.trade_date }]
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ kind, nodes }, meta)) })
    }
    if (path.match(/\/api\/stocks\/boards\/[^/]+\/constituents$/)) {
      const board = path.split('/').at(-2)
      const delay = board === 'BK0001' ? 90 : 10
      await new Promise((resolvePromise) => setTimeout(resolvePromise, delay))
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ kind: url.searchParams.get('kind') || 'industry', board_code: board, items: [{ code: board === 'BK0001' ? '600000.SS' : '300750.SZ', name: board === 'BK0001' ? '浦发银行' : '宁德时代', price: 10, change_pct: 1, rank: 1, trade_date: meta.trade_date }], meta })) })
    }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope([])) })
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => failures.push(`${viewport.width}px pageerror: ${error.message}`))
  page.on('console', (message) => { if (message.type() === 'error') failures.push(`${viewport.width}px console: ${message.text()}`) })
  // The app intentionally polls indices; wait for DOM readiness and explicit
  // assertions instead of networkidle so a long-lived polling request cannot
  // make this deterministic smoke test hang.
  await page.goto(`${baseURL}/`, { waitUntil: 'domcontentloaded' })
  await page.getByRole('region', { name: '热门板块' }).waitFor()
  await page.getByRole('region', { name: '热门题材' }).getByRole('button', { name: /新能源/ }).click()
  await page.getByText('宁德时代').waitFor()
  const cloud = page.getByRole('region', { name: '大盘云图' })
  await cloud.getByRole('combobox', { name: '快速定位行业' }).selectOption('BK0001')
  await cloud.getByRole('button', { name: '返回板块云图' }).waitFor()
  await cloud.getByRole('button', { name: '返回板块云图' }).click()
  const industryPanel = page.getByRole('region', { name: '热门板块' })
  await industryPanel.getByRole('button', { name: /银行/ }).click()
  await industryPanel.getByRole('button', { name: /电子/ }).click()
  await page.getByText('宁德时代').waitFor()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  if (overflow !== 0) failures.push(`${viewport.width}px horizontal overflow ${overflow}px`)
  await context.close()
}

// A separate deterministic error fixture verifies that one unavailable
// category does not blank the other category, and that a representative-stock
// snapshot keeps its own trade date rather than inheriting the hotspot date.
const failureContext = await browser.newContext({ viewport: { width: 390, height: 844 } })
await failureContext.addInitScript(() => localStorage.setItem('access_token', 'qa-home-token'))
await failureContext.route((url) => new URL(url).pathname.startsWith('/api/'), async (route) => {
  const requestURL = new URL(route.request().url())
  const path = requestURL.pathname
  if (path === '/api/auth/me') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ id: 1, username: 'qa-home', role: 'user', tier: 'A' })) })
  if (path === '/api/stocks/market-indices') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope([{ code: '000001.SS', name: '上证指数', price: 3200, change_pct: 0.8 }], meta)) })
  if (path === '/api/stocks/market-hotspots') {
    const kind = requestURL.searchParams.get('kind') || 'industry'
    if (kind === 'industry') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 'internal', message: '热门板块数据暂不可用，请稍后重试', data: null }) })
    const themeMeta = { ...meta, freshness: 'stale', provider: '历史快照', trade_date: '2026-08-23', data_at: '2026-08-23T07:30:00Z', fetched_at: '2026-08-23T07:30:01Z' }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ kind, items: [{ board_code: 'BK0002', board_name: '新能源', kind, change_pct: 2.1, hot_score: 91.2, rank: 1, trend_status: 'heating', streak_days: 3, trade_date: '2026-08-23' }] }, themeMeta)) })
  }
  if (path === '/api/stocks/market-cloud') {
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ kind: 'industry', nodes: [{ code: 'BK0001', name: '银行', kind: 'industry', value: 1, market_cap: null, change_pct: 1.2, trade_date: '2026-08-23' }] }, meta)) })
  }
  if (path.match(/\/api\/stocks\/boards\/[^/]+\/constituents$/)) {
    const board = path.split('/').at(-2)
    const staleMeta = { ...meta, freshness: 'stale', provider: '历史快照', trade_date: '2026-08-21', data_at: '2026-08-21T07:30:00Z', fetched_at: '2026-08-21T07:30:01Z' }
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ kind: 'theme', board_code: board, items: [{ code: '300750.SZ', name: '宁德时代', price: 200, change_pct: 1.1, rank: 1, trade_date: '2026-08-21' }] }, staleMeta)) })
  }
  return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope([])) })
})
const failurePage = await failureContext.newPage()
failurePage.on('pageerror', (error) => failures.push(`failure pageerror: ${error.message}`))
failurePage.on('console', (message) => {
  // Chromium reports the deliberately mocked HTTP 503 as a resource error;
  // only application console errors are failures in this scenario.
  if (message.type() === 'error' && !message.text().includes('status of 503')) failures.push(`failure console: ${message.text()}`)
})
await failurePage.goto(`${baseURL}/`, { waitUntil: 'domcontentloaded' })
await failurePage.getByRole('region', { name: '热门题材' }).waitFor()
await failurePage.getByRole('region', { name: '热门板块' }).getByRole('alert').filter({ hasText: '暂不可用' }).waitFor()
const failureTheme = failurePage.getByRole('region', { name: '热门题材' })
await failureTheme.getByText('历史数据').waitFor()
await failureTheme.getByText('交易日 2026-08-23').waitFor()
await failureTheme.getByRole('button', { name: /新能源/ }).click()
const failureStocks = failurePage.getByRole('region', { name: '代表个股' })
await failureStocks.getByText('宁德时代').waitFor()
await failureStocks.getByText('历史数据').waitFor()
await failureStocks.getByText('交易日 2026-08-21').waitFor()
await failurePage.getByRole('region', { name: '大盘云图' }).getByRole('combobox', { name: '快速定位行业' }).waitFor()
const failureOverflow = await failurePage.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
if (failureOverflow !== 0) failures.push(`failure 390px horizontal overflow ${failureOverflow}px`)
await failureContext.close()

await browser.close()
if (viteProcess) viteProcess.kill('SIGTERM')
console.log(JSON.stringify({ failures }, null, 2))
if (failures.length) process.exit(1)
