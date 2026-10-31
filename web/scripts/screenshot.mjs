import { chromium } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

const output = fileURLToPath(new URL('../../docs/assets/', import.meta.url))
await mkdir(output, { recursive: true })
const browser = await chromium.launch()
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1080 },
    deviceScaleFactor: 1,
  })
  page.setDefaultTimeout(15000)
  await page.goto(process.env.STUDIO_URL || 'http://127.0.0.1:4375')
  await page.getByRole('heading', { name: 'Overview', exact: true }).waitFor()
  await page.evaluate(() => document.fonts.ready)
  await page.screenshot({ path: `${output}/studio-overview.png` })
  await page
    .getByRole('navigation', { name: 'Main navigation' })
    .getByRole('button', { name: 'Style atlas' })
    .click()
  await page.mouse.move(0, 0)
  await page.screenshot({ path: `${output}/studio-atlas.png` })
  console.log(`Saved ${output}/studio-overview.png`)
} finally {
  await browser.close()
}
