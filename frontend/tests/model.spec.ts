import { test, expect } from '@playwright/test'

for (const width of [1440, 390]) {
  test(`model settings ${width}`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    const errors: string[] = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/archive/**', route => route.fulfill({ json: { list: [], total: 0 } }))
    let saved = { base_url: '', model: '', timeout: 120, key_configured: false }
    await page.route('**/api/model/settings', async route => {
      if (route.request().method() === 'PUT') {
        const value = route.request().postDataJSON()
        saved = { base_url: value.base_url, model: value.model, timeout: value.timeout, key_configured: value.clear_key ? false : !!value.api_key || saved.key_configured }
      }
      await route.fulfill({ json: saved })
    })
    await page.route('**/api/model/check', route => route.fulfill({ json: { connected: true, message: '固定文本测试通过' } }))
    await page.goto('http://127.0.0.1:8765')
    await page.getByRole('button', { name: '大模型设置', exact: true }).click()
    await expect(page.getByRole('heading', { name: '大模型设置', level: 1 })).toBeVisible()
    const inputs = page.locator('.config input')
    await inputs.nth(1).fill('http://example.com/v1')
    await expect(page.getByText('HTTP 不加密传输，API Key 和请求内容可能被截获，请仅用于可信网络。')).toBeVisible()
    await inputs.nth(2).fill('test-model')
    await inputs.nth(3).fill('fake-key')
    await page.getByRole('button', { name: '测试连接', exact: true }).click()
    await expect(page.getByText('固定文本测试通过')).toBeVisible()
    await expect(inputs.nth(3)).toHaveValue('')
    await page.getByText('清除已保存的 API Key', { exact: true }).click()
    await expect(page.getByRole('checkbox', { name: '清除已保存的 API Key' })).toBeChecked()
    await page.getByRole('button', { name: '保存配置', exact: true }).click()
    await expect(page.getByText('API Key（未配置，可选）')).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
    expect(errors).toEqual([])
    await page.screenshot({ path: `../data/screenshots/model-${width}.png`, fullPage: true })
  })
}
