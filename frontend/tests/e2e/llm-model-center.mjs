import { chromium } from 'playwright'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const externalBaseURL = process.env.QA_BASE_URL
const baseURL = externalBaseURL || 'http://127.0.0.1:4173'
const failures = []
const frontendDir = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

async function waitForServer(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok || response.status < 500) return
    } catch {
      // Vite is still starting.
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250))
  }
  throw new Error(`E2E server did not become ready: ${url}`)
}

let viteProcess
if (!externalBaseURL) {
  viteProcess = spawn('npx', ['vite', '--host', '127.0.0.1', '--port', '4173'], {
    cwd: frontendDir,
    stdio: 'inherit',
  })
  await waitForServer(baseURL)
}

const browser = await chromium.launch({ headless: true })

async function stopVite() {
  if (!viteProcess || viteProcess.exitCode !== null) return
  viteProcess.kill('SIGTERM')
  await new Promise((resolvePromise) => {
    const timer = setTimeout(resolvePromise, 3000)
    viteProcess.once('exit', () => {
      clearTimeout(timer)
      resolvePromise()
    })
  })
}

const baseModel = {
  id: 'cfg-deepseek',
  provider: 'deepseek',
  display_name: 'DeepSeek 主模型',
  model_name: 'deepseek-chat',
  base_url: 'https://api.deepseek.com/v1',
  key_hint: 'sk-****3557',
  lifecycle_status: 'draft',
  version: 1,
  runtime_fingerprint: 'fingerprint-1',
  verified_test_id: null,
  last_probe_status: null,
  last_probe_at: null,
  last_probe_latency_ms: null,
  input_price_micro_yuan_per_million: null,
  output_price_micro_yuan_per_million: null,
  max_output_tokens: 4096,
  created_new_version: false,
  supersedes_id: null,
  capabilities: { can_test: true, can_enable: true, can_disable: false, can_activate: true, can_delete: true },
}

const baseSettings = {
  id: 1,
  daily_token_limit: 2000000,
  budget_locked: false,
  budget_date: '2026-08-21',
  reserved_tokens: 0,
  settled_tokens: 0,
  default_model_config_id: null,
  version: 1,
  switched_by: null,
  switched_at: null,
}

function envelope(data, message = 'ok') {
  return { code: 0, message, data }
}

function responseFor(url, state) {
  const path = new URL(url).pathname
  if (path === '/api/auth/me') return envelope({ id: 1, username: 'qa-admin', email: 'qa@example.com', role: 'admin', tier: 'A' })
  if (path === '/api/admin/llm-models') {
    return envelope({ items: state.models, total: state.models.length, page: 1, page_size: 100, default_model_config_id: state.defaultId, daily_token_limit: state.settings.daily_token_limit, budget_locked: state.settings.budget_locked, settings_version: state.settings.version })
  }
  if (path === '/api/admin/llm-settings') return envelope(state.settings)
  if (path === '/api/admin/llm-usage') return envelope({ days: 7, items: [{ date: '2026-08-20', module: 'stock.analysis', provider: 'deepseek', model: 'deepseek-chat', model_config_id: 'cfg-deepseek', input_tokens: 12, output_tokens: 8, cost_micro_yuan: null, calls: 1 }], total_calls: 1, total_cost_micro_yuan: null })
  return envelope([])
}

async function setup(viewport) {
  const context = await browser.newContext({ viewport })
  await context.addInitScript(() => localStorage.setItem('access_token', 'qa-token'))
  const state = {
    models: [structuredClone(baseModel)],
    settings: structuredClone(baseSettings),
    defaultId: null,
    probes: new Map(),
    conflicts: 0,
    unsavedTestKeys: [],
  }
  await context.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    const method = request.method()
    if (path === '/api/admin/llm-models/test' && method === 'POST') {
      const body = request.postDataJSON()
      state.unsavedTestKeys.push(body.api_key)
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ status: 'success', test_run_id: 'probe-new', capabilities: { json: true } })) })
      return
    }
    if (path === '/api/admin/llm-models' && method === 'POST') {
      const body = request.postDataJSON()
      const created = {
        ...baseModel,
        id: `cfg-${body.provider}-${state.models.length}`,
        provider: body.provider,
        display_name: body.display_name,
        model_name: body.model_name,
        base_url: body.base_url,
        key_hint: 'sk-****3557',
        capabilities: { can_test: true, can_enable: true, can_disable: false, can_activate: true, can_delete: true },
      }
      state.models.push(created)
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope(created)) })
      return
    }
    if (path === '/api/admin/llm-models/cfg-deepseek/test' && method === 'POST') {
      state.probes.set('cfg-deepseek', 'probe-2')
      state.models[0] = { ...state.models[0], verified_test_id: 'probe-2', last_probe_status: 'success' }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ status: 'success', test_run_id: 'probe-2' })) })
      return
    }
    if (path === '/api/admin/llm-models/cfg-deepseek/enable' && method === 'POST') {
      state.models[0] = { ...state.models[0], lifecycle_status: 'active', version: 2, capabilities: { ...state.models[0].capabilities, can_enable: false, can_disable: true, can_delete: false } }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope(state.models[0])) })
      return
    }
    if (path === '/api/admin/llm-models/cfg-deepseek/activate' && method === 'POST') {
      state.defaultId = 'cfg-deepseek'
      state.settings = { ...state.settings, default_model_config_id: state.defaultId, version: 2 }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ ...state.models[0], lifecycle_status: 'active' })) })
      return
    }
    if (path === '/api/admin/llm-settings' && method === 'PATCH') {
      if (state.conflicts === 0) {
        state.conflicts += 1
        state.settings = { ...state.settings, daily_token_limit: 7654321, version: 3 }
        await route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({ code: 'llm_settings_conflict', message: '额度设置版本已变化', data: null, field: null, request_id: 'e2e-conflict' }),
        })
        return
      }
    }
    if (path === '/api/admin/llm-settings/unlock' && method === 'POST') {
      state.settings = { ...state.settings, budget_locked: false, reserved_tokens: 170, settled_tokens: 230, version: 5 }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope({ id: 1, daily_token_limit: state.settings.daily_token_limit, budget_locked: false, default_model_config_id: state.defaultId, version: 5 })) })
      return
    }
    const data = responseFor(request.url(), state)
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) })
  })
  const page = await context.newPage()
  page.on('dialog', (dialog) => { void dialog.accept() })
  page.on('pageerror', (error) => failures.push(`${viewport.width}px pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) {
      failures.push(`${viewport.width}px console: ${message.text()}`)
    }
  })
  return { context, page, state }
}

try {
for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  const { context, page, state } = await setup(viewport)
  await page.goto(`${baseURL}/admin`, { waitUntil: 'networkidle' })
  await page.getByRole('button', { name: '大模型配置' }).click()

  for (const candidate of [
    { provider: 'deepseek', name: 'DeepSeek E2E', model: 'deepseek-chat', baseURL: 'https://api.deepseek.com/v1' },
    { provider: 'kimi', name: 'Kimi E2E', model: 'moonshot-v1-8k', baseURL: 'https://api.moonshot.cn/v1' },
    { provider: 'qwen', name: 'Qwen E2E', model: 'qwen-max-long-context-model-id', baseURL: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  ]) {
    await page.getByRole('button', { name: '添加模型' }).click()
    await page.getByLabel('供应商').selectOption(candidate.provider)
    await page.getByLabel('配置名称').fill(candidate.name)
    await page.getByLabel('模型 ID').fill(candidate.model)
    await page.getByLabel('Base URL').fill(candidate.baseURL)
    await page.getByLabel('API Key').fill('sk-super-secret')
    await page.getByRole('button', { name: '测试配置' }).click()
    await page.getByText('测试完成，请重新输入 API Key 后保存').waitFor()
    if (await page.getByLabel('API Key').inputValue() !== '') failures.push(`${viewport.width}px API Key was not cleared after test`)
    if ((await page.locator('body').textContent())?.includes('sk-super-secret')) failures.push(`${viewport.width}px API Key entered DOM`)
    await page.getByLabel('API Key').fill('sk-resubmitted')
    await page.getByRole('button', { name: '保存配置' }).click()
    await page.getByText('模型配置已保存').waitFor()
  }
  if (state.unsavedTestKeys.length !== 3 || state.unsavedTestKeys.some((key) => key !== 'sk-super-secret')) {
    failures.push(`${viewport.width}px provider test requests did not carry the expected temporary key`)
  }

  const modelRow = page.getByTestId('llm-model-cfg-deepseek')
  await modelRow.getByRole('button', { name: '测试' }).click()
  await page.getByText('DeepSeek 主模型 测试完成').waitFor()
  await modelRow.getByRole('button', { name: '启用' }).click()
  await page.getByText('模型已启用').waitFor()
  await modelRow.getByRole('button', { name: '设为默认' }).click()
  await page.getByText('默认模型已切换').waitFor()
  if (!(await modelRow.getByRole('button', { name: '删除' }).isDisabled())) failures.push(`${viewport.width}px default delete was not disabled`)

  const limit = page.getByTestId('daily-token-limit')
  await limit.fill('123')
  await page.getByRole('button', { name: '保存限额' }).click()
  await page.getByText('配置已被其他管理员修改，请刷新后重试').waitFor()
  if (await limit.inputValue() !== '7654321') failures.push(`${viewport.width}px settings conflict did not refetch latest limit`)

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  if (overflow !== 0) failures.push(`${viewport.width}px admin page horizontal overflow ${overflow}px`)
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '大模型配置' }).click()
  if (await page.getByTestId('daily-token-limit').inputValue() !== '7654321') failures.push(`${viewport.width}px reload lost settings state`)
  await page.getByText('价格未知').first().waitFor()
  state.settings = { ...state.settings, budget_locked: true, reserved_tokens: 170, settled_tokens: 230, version: 4 }
  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: '大模型配置' }).click()
  await page.getByText('额度已锁停').waitFor()
  await page.getByText('预留 170').waitFor()
  await page.getByRole('button', { name: '解除锁停' }).click()
  await page.getByLabel('解锁原因').fill('已完成真实账单核对')
  await page.getByRole('button', { name: '确认解锁' }).click()
  await page.getByText('解锁审计已记录').waitFor()
  if (await page.getByText('额度已锁停').count() !== 0) failures.push(`${viewport.width}px budget lock banner did not clear after confirmed unlock`)
  await context.close()
}
} finally {
  await browser.close()
  await stopVite()
}
console.log(JSON.stringify({ failures }, null, 2))
if (failures.length) process.exit(1)
