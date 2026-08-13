import { chromium } from 'playwright'

const browser = await chromium.launch({ headless: true })
const failures = []
for (const viewport of [{ width: 390, height: 844 }, { width: 1440, height: 900 }]) {
  const context = await browser.newContext({ viewport })
  await context.addInitScript(() => localStorage.setItem('access_token', 'qa-token'))
  await context.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const url = new URL(route.request().url())
    let data = { code: 0, message: 'ok', data: [] }
    if (url.pathname === '/api/auth/me') data = { code: 0, message: 'ok', data: { id: 1, username: 'qa', email: 'qa@example.com', role: 'user', tier: 'A' } }
    else if (url.pathname === '/api/stocks/analyze' && route.request().method() === 'POST') data = { code: 0, message: 'ok', data: { tasks: [{ task_id: 1, stock_code: '600519' }] } }
    else if (url.pathname === '/api/news/collect' && route.request().method() === 'POST') data = { code: 0, message: 'ok', data: { task_id: 1 } }
    else if (url.pathname === '/api/tasks/1') data = { code: 0, message: 'ok', data: { id: 1, status: 'failed_unknown', progress: 0, error: '模型调用结果未知，任务未自动重试', result: null } }
    else if (url.pathname === '/api/news') data = { code: 0, message: 'ok', data: { items: [], total: 0 } }
    else if (url.pathname === '/api/news/sources') data = { code: 0, message: 'ok', data: [] }
    else if (url.pathname === '/api/stocks/user/results') data = { code: 0, message: 'ok', data: { items: [], total: 0, page: 1, page_size: 20 } }
    else if (url.pathname === '/api/membership/plans') data = { code: 0, message: 'ok', data: { plans: [], features: {} } }
    else if (url.pathname === '/api/membership/me') data = { code: 0, message: 'ok', data: { tier: 'A', raw_tier: 'A', tier_expire_at: null, is_trial: false, days_left: null } }
    else if (url.pathname === '/api/membership/usage') data = { code: 0, message: 'ok', data: {} }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) })
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => failures.push(`${viewport.width}px pageerror: ${error.message}`))
  page.on('console', (message) => { if (message.type() === 'error') failures.push(`${viewport.width}px console: ${message.text()}`) })

  await page.goto('http://127.0.0.1:5173/analysis', { waitUntil: 'networkidle' })
  await page.getByPlaceholder('输入股票代码，如 600519').fill('600519')
  await page.getByRole('button', { name: '开始分析' }).click()
  await page.waitForTimeout(2200)
  if (!(await page.getByText('模型调用结果未知，任务未自动重试').isVisible())) failures.push(`${viewport.width}px Analysis unknown error missing`)
  if (await page.getByRole('button', { name: '分析中...' }).count() !== 0) failures.push(`${viewport.width}px Analysis kept loading`)

  await page.goto('http://127.0.0.1:5173/news', { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '立即采集' }).click()
  await page.waitForTimeout(2200)
  if (!(await page.getByText('模型调用结果未知，任务未自动重试').isVisible())) failures.push(`${viewport.width}px News unknown error missing`)
  if (await page.getByRole('button', { name: '采集中…' }).count() !== 0) failures.push(`${viewport.width}px News kept collecting`)
  await context.close()
}
await browser.close()
console.log(JSON.stringify({ failures }, null, 2))
if (failures.length) process.exit(1)
