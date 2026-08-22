import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const baseURL = process.env.QA_BASE_URL || 'http://127.0.0.1:4174'
const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const screenshotsDir = resolve(frontendDir, '../docs/screenshots/datahub')
const failures = []
let viteProcess
const realApi = process.env.QA_REAL_API === '1'
const authToken = process.env.QA_AUTH_TOKEN || 'qa-e2e-token'

if (realApi && !process.env.QA_AUTH_TOKEN && process.env.QA_REAL_API_AUTH_MOCK !== '1') {
  throw new Error('QA_REAL_API=1 requires QA_AUTH_TOKEN, or explicitly set QA_REAL_API_AUTH_MOCK=1 for an auth-only mock')
}

async function waitForServer(url) {
  const deadline = Date.now() + 30000
  while (Date.now() < deadline) {
    try { const response = await fetch(url); if (response.ok) return } catch {}
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250))
  }
  throw new Error(`E2E server did not become ready: ${url}`)
}

if (!process.env.QA_BASE_URL) {
  viteProcess = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', '4174'], { cwd: frontendDir, stdio: 'inherit' })
  await waitForServer(baseURL)
}

const browser = await chromium.launch({ headless: true })
function envelope(data, message = 'ok') { return { code: 0, message, data } }
const sources = [{
  id: null, provider: 'tushare', display_name: 'Tushare Pro', description: 'Token 盘后数据',
  capabilities: ['kpl.limit_list'], auth_type: 'token', credential_fields: ['token'], fee_type: '积分/付费',
  update_frequency: '盘后', risk_note: '逐能力探测', enabled: false, version: 0, key_hint: null,
  fingerprint: null, last_probe_status: null, last_probe_at: null, last_probe_latency_ms: null,
}]
const routes = [{ capability: 'market.indices', mode: 'auto', providers: ['tencent', 'akshare'], contract_version: '1.0', version: 0 }]

for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  const context = await browser.newContext({ viewport })
  await context.addInitScript((token) => localStorage.setItem('access_token', token), authToken)
  if (!realApi) {
    await context.route((url) => new URL(url).pathname.startsWith('/api/'), async (route) => {
      const path = new URL(route.request().url()).pathname
      if (path === '/api/auth/me') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ id: 1, username: 'qa-admin', role: 'admin', tier: 'A' })) })
      if (path === '/api/admin/stats') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ total_users: 1, active_users: 1, admin_count: 1, active_plans: 1, total_usage_count: 0 })) })
      if (path === '/api/admin/data-sources') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ items: sources })) })
      if (path === '/api/admin/data-source-routes') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ items: routes })) })
      if (path === '/api/stocks/market-indices') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ...envelope([{ code: '000001', name: '上证指数', price: 3000, change_pct: 1 }]), meta: { freshness: 'stale', provider: '腾讯财经', data_at: '2026-08-22T07:30:00Z' } }) })
      if (path === '/api/stocks/sectors/overview') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ category: '银行金融', period: '1月', sectors: [], stocks: [] })) })
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({})) })
    })
  } else if (process.env.QA_REAL_API_AUTH_MOCK === '1') {
    // Only identity is mocked in this mode; DataHub and market requests stay real.
    await context.route((url) => new URL(url).pathname === '/api/auth/me', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ id: 1, username: 'qa-admin', role: 'admin', tier: 'A' })) })
    })
  }
  const page = await context.newPage()
  page.on('pageerror', (error) => failures.push(`${viewport.width}px pageerror: ${error.message}`))
  page.on('console', (message) => { if (message.type() === 'error' && !message.text().includes('Failed to load resource')) failures.push(`${viewport.width}px console: ${message.text()}`) })
  await page.goto(`${baseURL}/admin`, { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '数据源配置' }).click()
  if (!realApi) {
    await page.getByText('Tushare Pro').waitFor()
    await page.getByLabel('Token').fill('secret-e2e-token')
    await page.getByRole('button', { name: '测试连接' }).click()
    await page.waitForFunction(() => document.querySelector('#tushare-token')?.value === '')
    if (await page.getByLabel('Token').inputValue() !== '') failures.push(`${viewport.width}px Token was not cleared`)
  } else {
    // Real mode exercises a real backend probe without mutating route state.
    // Sina is a free independent source and its card is deterministic; the
    // probe response must be visible before we inspect the read-only routes.
    const sinaCard = page.locator('section.card').filter({ hasText: '新浪财经' }).first()
    await sinaCard.getByRole('button', { name: '测试连接' }).click()
    await page.getByRole('status').filter({ hasText: /新浪财经 测试完成：获取 \d+ 行/ }).waitFor()
    const routeCard = page.locator('section.card').filter({ hasText: '能力路由' }).first()
    await routeCard.getByText('大盘指数').waitFor()
    if (!(await routeCard.getByText(/腾讯财经|新浪财经/).count())) failures.push(`${viewport.width}px route candidates are missing`)
    await page.getByRole('heading', { name: '数据源配置' }).waitFor()
  }
  await page.screenshot({ path: resolve(screenshotsDir, `admin-${viewport.width}.png`), fullPage: true })
  if (await page.locator('[role="alert"]').count()) failures.push(`${viewport.width}px admin displayed an error alert`)
  await page.goto(`${baseURL}/`, { waitUntil: 'networkidle' })
  if (!realApi) await page.getByText(/最近有效行情：腾讯财经/).waitFor()
  else await page.getByText('大盘指数', { exact: true }).waitFor()
  await page.screenshot({ path: resolve(screenshotsDir, `home-${viewport.width}.png`), fullPage: true })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  if (overflow !== 0) failures.push(`${viewport.width}px horizontal overflow ${overflow}px`)
  await context.close()
}

await browser.close()
if (viteProcess) viteProcess.kill('SIGTERM')
console.log(JSON.stringify({ failures, screenshotsDir }, null, 2))
if (failures.length) process.exit(1)
