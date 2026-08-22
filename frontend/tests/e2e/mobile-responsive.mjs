import { chromium } from 'playwright'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'

const baseURL = process.env.QA_BASE_URL || 'http://127.0.0.1:5173'
const screenshotDir = resolve('../docs/screenshots/mobile-responsive')
await mkdir(screenshotDir, { recursive: true })

const browser = await chromium.launch({ headless: true })
console.log('QA: browser launched')
const failures = []
const results = []

const apiResponse = (url) => {
  if (url.includes('/api/auth/me')) {
    return { code: 0, message: 'ok', data: { id: 1, username: 'qa-admin', email: 'qa@example.com', role: 'admin', tier: 'A' } }
  }
  if (url.includes('/api/stocks/market-indices')) {
    return { code: 0, message: 'ok', data: [
      { code: '000001', name: '上证指数', price: 3388.81, change_pct: 0.72 },
      { code: '399001', name: '深证成指', price: 10987.26, change_pct: -0.31 },
      { code: '399006', name: '创业板指', price: 2288.16, change_pct: 1.12 },
      { code: '000688', name: '科创50', price: 1026.55, change_pct: -0.18 },
    ] }
  }
  if (url.includes('/api/stocks/market-hotspots')) {
    const kind = new URL(url).searchParams.get('kind') || 'industry'
    return { code: 0, message: 'ok', data: { kind, items: [{ board_code: kind === 'industry' ? 'BK0001' : 'BK0002', board_name: kind === 'industry' ? '银行' : '新能源', kind, change_pct: 1.2, hot_score: 80, rank: 1, trend_status: 'steady', trade_date: '2026-08-22' }] }, meta: { provider: '东方财富', freshness: 'fresh', trade_date: '2026-08-22' } }
  }
  if (url.includes('/api/stocks/market-cloud')) {
    return { code: 0, message: 'ok', data: { kind: 'industry', nodes: [{ code: 'BK0001', name: '银行', kind: 'industry', value: 100, market_cap: 100, change_pct: 1.2 }] }, meta: { provider: '东方财富', freshness: 'fresh', trade_date: '2026-08-22' } }
  }
  if (url.includes('/api/stocks/boards/')) {
    return { code: 0, message: 'ok', data: { items: [{ code: '600036.SS', name: '招商银行', price: 42.18, change_pct: 0.86, rank: 1 }] }, meta: { provider: '东方财富', freshness: 'fresh', trade_date: '2026-08-22' } }
  }
  if (url.includes('/api/stocks/portfolio/stocks')) {
    return { code: 0, message: 'ok', data: [] }
  }
  if (url.includes('/api/stocks/portfolio/summary')) {
    return { code: 0, message: 'ok', data: {
      total_stocks: 0,
      total_cost: 0,
      total_market_value: 0,
      total_profit_loss: 0,
      total_profit_pct: 0,
      monitoring_count: 0,
    } }
  }
  if (url.includes('/api/membership/plans')) {
    return { code: 0, message: 'ok', data: {
      plans: [
        { code: 'free', name: '免费档', price_monthly_cents: 0, price_yearly_cents: 0, quotas: { stock_analysis: 1 }, sort_order: 0 },
        { code: 'A', name: 'A 档会员', price_monthly_cents: 9900, price_yearly_cents: 99000, quotas: { stock_analysis: 10 }, sort_order: 1 },
      ],
      features: { stock_analysis: '个股分析' },
    } }
  }
  if (url.includes('/api/membership/me')) {
    return { code: 0, message: 'ok', data: { tier: 'A', raw_tier: 'A', tier_expire_at: null, is_trial: false, days_left: null } }
  }
  if (url.includes('/api/membership/usage')) {
    return { code: 0, message: 'ok', data: { stock_analysis: { name: '个股分析', used: 2, limit: 10, remaining: 8 } } }
  }
  return { code: 0, message: 'ok', data: [] }
}

async function setupPage(viewport) {
  const context = await browser.newContext({ viewport })
  await context.addInitScript(() => localStorage.setItem('access_token', 'qa-token'))
  await context.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(apiResponse(route.request().url())) })
  })
  const page = await context.newPage()
  page.on('pageerror', (error) => failures.push(`${viewport.width}px pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error') failures.push(`${viewport.width}px console: ${message.text()}`)
  })
  return { context, page }
}

for (const viewport of [
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 768, height: 1024 },
  { width: 1440, height: 900 },
]) {
  console.log(`QA: start ${viewport.width}x${viewport.height}`)
  const { context, page } = await setupPage(viewport)
  await page.goto(baseURL, { waitUntil: 'networkidle' })
  const mobile = viewport.width <= 900
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  const menuVisible = await page.getByRole('button', { name: '打开导航菜单' }).isVisible()
  const sidebarWidth = await page.locator('#app-navigation').evaluate((element) => element.getBoundingClientRect().width)
  const indexColumns = await page.locator('.home-index-grid').evaluate((element) => getComputedStyle(element).gridTemplateColumns.split(' ').length)

  results.push({ viewport: `${viewport.width}x${viewport.height}`, overflow, menuVisible, sidebarWidth, indexColumns })
  if (overflow !== 0) failures.push(`${viewport.width}px 首页整体横向溢出 ${overflow}px`)
  if (menuVisible !== mobile) failures.push(`${viewport.width}px 菜单按钮显示状态错误`)
  if (viewport.width === 390 && indexColumns !== 2) failures.push('390px 首页指数未显示为双列')
  if (viewport.width === 1440 && Math.round(sidebarWidth) !== 232) failures.push(`1440px 桌面侧栏宽度为 ${sidebarWidth}px`)

  await page.screenshot({ path: resolve(screenshotDir, `${viewport.width}-home.png`), fullPage: true })

  if (mobile) {
    await page.getByRole('button', { name: '打开导航菜单' }).click()
    const navVisible = await page.locator('#app-navigation').getAttribute('aria-hidden')
    const bodyOverflow = await page.evaluate(() => document.body.style.overflow)
    if (navVisible !== 'false') failures.push(`${viewport.width}px 抽屉打开状态错误`)
    if (bodyOverflow !== 'hidden') failures.push(`${viewport.width}px 抽屉打开后背景未锁定`)
    if (viewport.width === 390) await page.screenshot({ path: resolve(screenshotDir, '390-drawer-open.png') })
    await page.getByRole('link', { name: '股票分析' }).click()
    await page.waitForTimeout(50)
    if (await page.locator('#app-navigation').getAttribute('aria-hidden') !== 'true') failures.push(`${viewport.width}px 路由切换后抽屉未关闭`)
  }

  for (const route of ['/analysis', '/main-force', '/portfolio', '/realtime', '/membership', '/guide', '/admin']) {
    await page.goto(`${baseURL}${route}`, { waitUntil: 'networkidle' })
    const pageOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    if (pageOverflow !== 0) failures.push(`${viewport.width}px ${route} 整体横向溢出 ${pageOverflow}px`)
  }

  if (viewport.width === 390) {
    await page.goto(`${baseURL}/membership`, { waitUntil: 'networkidle' })
    await page.screenshot({ path: resolve(screenshotDir, '390-wide-table.png'), fullPage: true })
  }

  await context.close()
  console.log(`QA: finish ${viewport.width}x${viewport.height}`)
}

const loginContext = await browser.newContext({ viewport: { width: 390, height: 844 } })
const loginPage = await loginContext.newPage()
await loginPage.goto(`${baseURL}/login`, { waitUntil: 'networkidle' })
const loginOverflow = await loginPage.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
if (loginOverflow !== 0) {
  const offenders = await loginPage.evaluate(() => [...document.querySelectorAll('*')]
    .filter((element) => {
      const rect = element.getBoundingClientRect()
      return rect.left < 0 || rect.right > document.documentElement.clientWidth
    })
    .map((element) => ({ tag: element.tagName, className: element.className, rect: element.getBoundingClientRect().toJSON() }))
    .slice(0, 5))
  failures.push(`390px 登录页整体横向溢出 ${loginOverflow}px: ${JSON.stringify(offenders)}`)
}
await loginPage.screenshot({ path: resolve(screenshotDir, '390-login.png'), fullPage: true })
await loginContext.close()
console.log('QA: login checked')

await browser.close()
console.log(JSON.stringify({ results, failures }, null, 2))
if (failures.length > 0) process.exit(1)
