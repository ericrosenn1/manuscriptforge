import { expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { createDemoWorkspace } from '../src/lib/analysis'
import { STORAGE_KEY } from '../src/lib/storage'

test('corrupt saved data stays intact while the temporary demo runs and raw recovery is exact', async ({
  page,
}, testInfo) => {
  const raw =
    '{"schemaVersion":1,"name":"Unfinished writing — keep me","manuscript":"Original notes"'
  await page.addInitScript(
    ({ key, value }) => {
      if (localStorage.getItem(key) === null) localStorage.setItem(key, value)
    },
    { key: STORAGE_KEY, value: raw },
  )
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('Your stored project is preserved')
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe(raw)

  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Recover saved data', exact: true }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe('manuscriptforge-recovery.json')
  const destination = testInfo.outputPath('raw-recovery.json')
  await download.saveAs(destination)
  expect(await readFile(destination, 'utf-8')).toBe(raw)

  await page
    .getByRole('navigation', { name: 'Main navigation' })
    .getByRole('button', { name: 'Writing desk', exact: true })
    .click()
  await page
    .getByLabel('Manuscript text')
    .fill('Temporary demo edits must never overwrite the recovery source.')
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe(raw)
  await page.reload()
  await expect(page.getByRole('alert')).toContainText('Your stored project is preserved')
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe(raw)
})

test('a structurally valid workspace with mismatched source hashes is preserved for recovery', async ({
  page,
}, testInfo) => {
  const workspace = await createDemoWorkspace()
  workspace.documents[0].text += '\nA modification with no matching recorded hash.'
  const raw = JSON.stringify(workspace)
  await page.addInitScript(({ key, value }) => localStorage.setItem(key, value), {
    key: STORAGE_KEY,
    value: raw,
  })
  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('Your stored project is preserved')
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe(raw)
  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Recover saved data', exact: true }).click()
  const destination = testInfo.outputPath('hash-mismatch-recovery.json')
  await (await downloadEvent).saveAs(destination)
  expect(await readFile(destination, 'utf-8')).toBe(raw)
})

test('creating a new empty workspace replaces the active project only after confirmation', async ({
  page,
}) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Overview', exact: true })).toBeVisible()
  const original = await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)
  expect(JSON.parse(original!).documents).toHaveLength(3)
  await page.locator('.project-picker').click()
  await page.getByLabel('Workspace name').fill('A clean writing project')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await expect(page.getByRole('dialog', { name: 'Replace workspace' })).toBeVisible()
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe(original)
  await page.getByRole('button', { name: 'Close dialog', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY)).toBe(original)

  await page.locator('.project-picker').click()
  await page.getByLabel('Workspace name').fill('A clean writing project')
  await page.getByRole('button', { name: 'Continue', exact: true }).click()
  await page.getByRole('button', { name: 'Open workspace', exact: true }).click()
  await expect
    .poll(
      async () =>
        JSON.parse((await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY))!).name,
    )
    .toBe('A clean writing project')
  const stored = JSON.parse((await page.evaluate((key) => localStorage.getItem(key), STORAGE_KEY))!)
  expect(stored.documents).toEqual([])
  expect(stored.passages).toEqual([])
  expect(stored.manuscript).toBe('')
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
