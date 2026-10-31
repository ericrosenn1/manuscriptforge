import { test, expect, type Page } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { Document, HeadingLevel, Packer, Paragraph } from 'docx'
import JSZip from 'jszip'

async function navigate(page: Page, name: string) {
  await page
    .getByRole('navigation', { name: 'Main navigation' })
    .getByRole('button', { name })
    .click()
}

async function openStudio(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Overview', exact: true })).toBeVisible()
}

function samplePdf(): Buffer {
  const stream =
    'BT /F1 12 Tf 50 740 Td (Methods) Tj 0 -24 Td (An original PDF passage for local import verification.) Tj ET'
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
  ]
  let body = '%PDF-1.4\n'
  const offsets = [0]
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(body))
    body += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xref = Buffer.byteLength(body)
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  body += offsets
    .slice(1)
    .map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`)
    .join('')
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(body)
}

test('synthetic sources remain inspectable and duplicate exclusion is explicit', async ({
  page,
}) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await openStudio(page)
  await navigate(page, 'Source library')
  await expect(page.getByRole('row', { name: /protocol\.md/ })).toBeVisible()
  await expect(page.getByRole('row', { name: /methods_duplicate\.docx/ })).toBeVisible()
  await page.getByRole('button', { name: /protocol\.md/ }).click()
  await expect(page.getByRole('region', { name: 'Source preview: protocol.md' })).toContainText(
    'This synthetic collection',
  )
  await navigate(page, 'Passage review')
  await page.getByRole('button', { name: 'Excluded', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Selected passage' })).toContainText(
    'Identical text appears elsewhere',
  )
  await expect(page.getByLabel('Passage review note')).toHaveValue(
    /excluded to avoid duplicate weighting/,
  )
  expect(errors).toEqual([])
})

test('a real Markdown import starts pending and explicit review updates the profile', async ({
  page,
}) => {
  await openStudio(page)
  await page.getByLabel('Import source documents').setInputFiles({
    name: 'review-fixture.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(
      '## Methods\n\nAn original imported procedure records every change.\n\n## References\n\nThis reference must not enter the profile.',
    ),
  })
  await expect(page.getByRole('row', { name: /review-fixture\.md/ })).toContainText('1 pending')
  await navigate(page, 'Passage review')
  await page.getByLabel('Search passages').fill('review-fixture.md')
  await expect(page.getByRole('region', { name: 'Selected passage' })).toContainText(
    'An original imported procedure records every change.',
  )
  await page
    .getByLabel('Passage review note')
    .fill('Reviewed: include this original methods example.')
  await page.getByRole('button', { name: 'Approve passage', exact: true }).click()
  await expect(page.getByLabel('Passage review totals')).toContainText('7 approved')
  await expect(page.getByLabel('Passage review totals')).toContainText('0 awaiting review')
  await page.reload()
  await navigate(page, 'Passage review')
  await page.getByLabel('Search passages').fill('review-fixture.md')
  await expect(page.getByLabel('Passage review note')).toHaveValue(
    'Reviewed: include this original methods example.',
  )
  await expect(page.getByRole('region', { name: 'Selected passage' })).toContainText('Approved')
})

test('the production build extracts DOCX and PDF content without a server upload', async ({
  page,
}) => {
  const requests: string[] = []
  page.on('request', (request) => {
    if (request.method() !== 'GET') requests.push(`${request.method()} ${request.url()}`)
  })
  await openStudio(page)
  const docx = await Packer.toBuffer(
    new Document({
      sections: [
        {
          children: [
            new Paragraph({ text: 'Methods', heading: HeadingLevel.HEADING_1 }),
            new Paragraph('An original Word passage verifies the local document parser.'),
          ],
        },
      ],
    }),
  )
  await page.getByLabel('Import source documents').setInputFiles([
    {
      name: 'word-fixture.docx',
      mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
      buffer: docx,
    },
    { name: 'pdf-fixture.pdf', mimeType: 'application/pdf', buffer: samplePdf() },
  ])
  await expect(page.getByRole('row', { name: /word-fixture\.docx/ })).toBeVisible()
  await expect(page.getByRole('row', { name: /pdf-fixture\.pdf/ })).toBeVisible()
  await page.getByRole('button', { name: /pdf-fixture\.pdf/ }).click()
  await expect(page.getByRole('region', { name: 'Source preview: pdf-fixture.pdf' })).toContainText(
    'An original PDF passage',
  )
  await page.getByRole('button', { name: /word-fixture\.docx/ }).click()
  await expect(
    page.getByRole('region', { name: 'Source preview: word-fixture.docx' }),
  ).toContainText('An original Word passage')
  expect(requests).toEqual([])
})

test('the manuscript persists and its preview treats HTML as text', async ({ page }) => {
  await openStudio(page)
  await navigate(page, 'Writing desk')
  const draft =
    '# My reproducible draft\n\n## Methods\n\nA **carefully reviewed** sentence.\n\n<img src=x onerror="window.__injected=true">'
  await page.getByLabel('Manuscript text').fill(draft)
  await page.getByRole('button', { name: 'Preview', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'My reproducible draft' })).toBeVisible()
  await expect(page.getByRole('article', { name: 'Manuscript preview' })).toContainText(
    '<img src=x',
  )
  expect(await page.evaluate(() => '__injected' in window)).toBe(false)
  await page.reload()
  await navigate(page, 'Writing desk')
  await expect(page.getByLabel('Manuscript text')).toHaveValue(draft)
})

test('portable exports have valid contents and a JSON backup restores the draft', async ({
  page,
}, testInfo) => {
  await openStudio(page)
  await navigate(page, 'Writing desk')
  await page
    .getByLabel('Manuscript text')
    .fill('# Portable manuscript\n\nA user-authored draft survives a round trip.')
  await navigate(page, 'Export & provenance')

  const archiveEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: /Download workspace/ }).click()
  const archive = await archiveEvent
  const archivePath = testInfo.outputPath('workspace.zip')
  await archive.saveAs(archivePath)
  const zip = await JSZip.loadAsync(await readFile(archivePath))
  const manifest = JSON.parse(await zip.file('manifest.json')!.async('string')) as {
    files: { path: string; bytes: number; sha256: string }[]
  }
  expect(manifest.files.map((file) => file.path)).toEqual(
    expect.arrayContaining([
      'project.json',
      'manuscript.md',
      'analysis.json',
      'curation.csv',
      'report.md',
      'README.md',
    ]),
  )
  for (const file of manifest.files) {
    const bytes = await zip.file(file.path)!.async('nodebuffer')
    expect(bytes.byteLength).toBe(file.bytes)
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(file.sha256)
  }

  const docxEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download .docx', exact: true }).click()
  const docxPath = testInfo.outputPath('manuscript.docx')
  await (await docxEvent).saveAs(docxPath)
  const docx = await JSZip.loadAsync(await readFile(docxPath))
  expect(await docx.file('word/document.xml')!.async('string')).toContain('Portable manuscript')

  const backupEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download .json', exact: true }).click()
  const backupPath = testInfo.outputPath('project.json')
  await (await backupEvent).saveAs(backupPath)
  await navigate(page, 'Writing desk')
  await page.getByLabel('Manuscript text').fill('This temporary edit will be replaced.')
  await page.getByLabel('Restore project JSON').setInputFiles(backupPath)
  await expect(page.getByRole('dialog', { name: 'Replace workspace' })).toBeVisible()
  await page.getByRole('button', { name: 'Open workspace', exact: true }).click()
  await navigate(page, 'Writing desk')
  await expect(page.getByLabel('Manuscript text')).toHaveValue(
    '# Portable manuscript\n\nA user-authored draft survives a round trip.',
  )
})

test('invalid project imports preserve the active workspace', async ({ page }) => {
  await openStudio(page)
  await page.getByLabel('Restore project JSON').setInputFiles({
    name: 'invalid.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{"schemaVersion":99}'),
  })
  await expect(page.getByRole('status')).toContainText('Unsupported project version')
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await navigate(page, 'Source library')
  await expect(page.getByRole('row', { name: /protocol\.md/ })).toBeVisible()
})

test('mobile navigation and writing remain within the viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await openStudio(page)
  await page.getByRole('button', { name: 'Open navigation' }).click()
  await navigate(page, 'Writing desk')
  await expect(page.getByLabel('Manuscript text')).toBeVisible()
  const overflows = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  )
  expect(overflows).toBe(false)
})
