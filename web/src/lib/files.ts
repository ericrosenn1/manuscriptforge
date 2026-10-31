import type { Passage, SourceDocument, Workspace } from './types'
import { analyzeWorkspace, normalizeText } from './analysis'

export const MAX_FILE_BYTES = 10 * 1024 * 1024
export const MAX_PDF_PAGES = 50
const MAX_WORKSPACE_BYTES = 20 * 1024 * 1024
const SECTIONS = new Set([
  'abstract',
  'introduction',
  'methods',
  'results',
  'discussion',
  'limitations',
  'conclusion',
  'other',
])
const DECISIONS = new Set(['pending', 'approved', 'excluded'])

/** A basename only, suitable for a downloaded file or a single ZIP entry. */
export function safeFilename(name: string): string {
  const cleaned = name
    .normalize('NFKC')
    .replace(/[\\/\x00-\x1f\x7f<>:"|?*]/g, '-')
    .replace(/^\.+|[. ]+$/g, '')
    .trim()
  const bounded = cleaned.slice(0, 100) || 'untitled'
  return /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(bounded) ? `file-${bounded}` : bounded
}

export async function readDocumentFile(
  file: File,
): Promise<{ name: string; text: string; format: string }> {
  if (file.size > MAX_FILE_BYTES) throw new Error(`${file.name}: the maximum file size is 10 MB.`)
  const format = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (!['md', 'txt', 'docx', 'pdf'].includes(format))
    throw new Error(`${file.name}: choose a Markdown, TXT, DOCX, or PDF document.`)
  let text: string
  if (format === 'md' || format === 'txt') {
    const buffer = await file.arrayBuffer()
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(buffer)
    } catch {
      throw new Error(`${file.name}: save this text document as UTF-8 and try again.`)
    }
  } else if (format === 'docx') {
    const mammoth = await import('mammoth')
    const result = await mammoth.extractRawText({ arrayBuffer: await file.arrayBuffer() })
    text = result.value
  } else {
    const pdfjs = await import('pdfjs-dist')
    const worker = await import('pdfjs-dist/build/pdf.worker.min.mjs?url')
    pdfjs.GlobalWorkerOptions.workerSrc = worker.default
    const task = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
    try {
      const pdf = await task.promise
      if (pdf.numPages > MAX_PDF_PAGES)
        throw new Error(
          `${file.name}: PDFs may contain at most ${MAX_PDF_PAGES} pages. Split the document and import smaller files.`,
        )
      const pages: string[] = []
      for (let index = 1; index <= pdf.numPages; index += 1) {
        const page = await pdf.getPage(index)
        const content = await page.getTextContent()
        pages.push(
          content.items
            .map((item) => ('str' in item ? `${item.str}${item.hasEOL ? '\n' : ' '}` : ''))
            .join(''),
        )
        page.cleanup()
      }
      text = pages.join('\n\n')
    } finally {
      await task.destroy()
    }
  }
  text = text
    .replace(/\r\n?/g, '\n')
    .replace(/\u0000/g, '')
    .trim()
  if (!text)
    throw new Error(
      `${file.name}: no readable text was found. Scanned PDFs need OCR before import.`,
    )
  if (text.length > MAX_FILE_BYTES)
    throw new Error(`${file.name}: extracted text exceeds the 10 MB limit.`)
  return { name: file.name, text, format }
}

function record(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error(`Invalid project: ${label} must be an object.`)
  return value as Record<string, unknown>
}

function string(value: unknown, label: string, limit = MAX_FILE_BYTES, nonempty = false): string {
  if (typeof value !== 'string' || value.length > limit || (nonempty && !value.trim()))
    throw new Error(
      `Invalid project: ${label} must be ${nonempty ? 'a nonempty' : 'a'} string of at most ${limit} characters.`,
    )
  return value
}

function timestamp(value: unknown, label: string): string {
  const date = string(value, label, 64, true)
  if (!Number.isFinite(Date.parse(date)))
    throw new Error(`Invalid project: ${label} is not a valid timestamp.`)
  return date
}

function hash(value: unknown, label: string): string {
  const result = string(value, label, 64, true)
  if (!/^[a-f\d]{64}$/i.test(result))
    throw new Error(`Invalid project: ${label} is not a SHA-256 digest.`)
  return result
}

/** Validate and reconstruct only supported fields; never spread untrusted JSON into app state. */
export function parseWorkspace(json: string): Workspace {
  if (json.length > MAX_WORKSPACE_BYTES)
    throw new Error('Invalid project: JSON exceeds the 20 MB limit.')
  let decoded: unknown
  try {
    decoded = JSON.parse(json)
  } catch {
    throw new Error('Invalid project: this file is not valid JSON.')
  }
  const raw = record(decoded, 'root')
  if (raw.schemaVersion !== 1)
    throw new Error(
      'Unsupported project version. Choose a ManuscriptForge version 1 project export.',
    )
  if (!Array.isArray(raw.documents) || raw.documents.length > 500)
    throw new Error('Invalid project: documents must be an array with at most 500 entries.')
  if (!Array.isArray(raw.passages) || raw.passages.length > 10000)
    throw new Error('Invalid project: passages must be an array with at most 10,000 entries.')
  const documentIds = new Set<string>()
  const documents: SourceDocument[] = raw.documents.map((value, index) => {
    const source = record(value, `document ${index + 1}`)
    const id = string(source.id, 'document ID', 200, true)
    if (documentIds.has(id)) throw new Error('Invalid project: document IDs must be unique.')
    documentIds.add(id)
    if (typeof source.synthetic !== 'boolean')
      throw new Error('Invalid project: every document needs a synthetic-data flag.')
    const format = string(source.format, 'document format', 20, true)
    if (!['md', 'txt', 'docx', 'pdf'].includes(format))
      throw new Error('Invalid project: unsupported source format.')
    return {
      id,
      name: string(source.name, 'document name', 500, true),
      format,
      text: string(source.text, 'document text', MAX_FILE_BYTES, true),
      hash: hash(source.hash, 'document hash'),
      addedAt: timestamp(source.addedAt, 'document date'),
      synthetic: source.synthetic,
    }
  })
  const passageIds = new Set<string>()
  const passages: Passage[] = raw.passages.map((value, index) => {
    const item = record(value, `passage ${index + 1}`)
    const id = string(item.id, 'passage ID', 200, true)
    if (passageIds.has(id)) throw new Error('Invalid project: passage IDs must be unique.')
    passageIds.add(id)
    const documentId = string(item.documentId, 'source document ID', 200, true)
    if (!documentIds.has(documentId))
      throw new Error('Invalid project: a passage refers to a missing source document.')
    const section = string(item.section, 'passage section', 30)
    const decision = string(item.decision, 'passage decision', 30)
    if (!SECTIONS.has(section) || !DECISIONS.has(decision))
      throw new Error('Invalid project: unknown passage section or review decision.')
    return {
      id,
      documentId,
      section: section as Passage['section'],
      decision: decision as Passage['decision'],
      text: string(item.text, 'passage text', MAX_FILE_BYTES, true),
      hash: hash(item.hash, 'passage hash'),
      note: string(item.note, 'review note', 10000),
    }
  })
  return {
    schemaVersion: 1,
    id: string(raw.id, 'project ID', 200, true),
    name: string(raw.name, 'project name', 500, true),
    description: string(raw.description, 'project description', 10000),
    documents,
    passages,
    manuscript: string(raw.manuscript, 'manuscript'),
    createdAt: timestamp(raw.createdAt, 'creation date'),
    updatedAt: timestamp(raw.updatedAt, 'update date'),
  }
}

/** Verify recorded digests and source links before trusting an imported or saved workspace. */
export async function verifyWorkspaceHashes(workspace: Workspace): Promise<void> {
  const sources = new Map<string, string>()
  for (const document of workspace.documents) {
    const text = normalizeText(document.text)
    if ((await sha256(text)) !== document.hash.toLowerCase()) {
      throw new Error(
        `The source fingerprint for ${document.name} does not match its text. Restore an unmodified project export.`,
      )
    }
    sources.set(document.id, text)
  }
  for (const passage of workspace.passages) {
    const text = normalizeText(passage.text)
    if ((await sha256(text)) !== passage.hash.toLowerCase()) {
      throw new Error(
        `Passage ${passage.id} has been changed without updating its fingerprint. Restore an unmodified project export.`,
      )
    }
    if (!sources.get(passage.documentId)?.includes(text)) {
      throw new Error(`Passage ${passage.id} cannot be traced to the text of its source document.`)
    }
  }
}

/** Escape spreadsheet formulas as well as CSV delimiters. */
export function csvCell(value: string | number): string {
  let text = String(value)
  if (/^[\s]*[=+\-@]/.test(text) || /^[\t\r\n]/.test(text)) text = `'${text}`
  return `"${text.replace(/"/g, '""')}"`
}

async function sha256(text: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}

function report(workspace: Workspace): string {
  const analysis = analyzeWorkspace(workspace)
  return [
    `# ${workspace.name} — corpus report`,
    '',
    workspace.description,
    '',
    `Generated from project revision ${workspace.updatedAt}.`,
    '',
    `- Documents: ${workspace.documents.length}`,
    `- Passages: ${workspace.passages.length}`,
    `- Approved: ${analysis.approvedCount}; pending: ${analysis.pendingCount}; excluded: ${analysis.excludedCount}`,
    `- Approved words: ${analysis.wordCount}`,
    `- Sentences: ${analysis.sentenceCount}`,
    `- Mean words per sentence: ${analysis.averageSentenceLength.toFixed(1)}`,
    `- Distinct-word proportion: ${(analysis.lexicalDiversity * 100).toFixed(1)}%`,
    '',
    'Only approved passages contribute to these descriptive statistics. Rule-based indicators do not establish writing quality, authorship, scientific validity, or stylistic imitation.',
    '',
    '## Section coverage',
    '',
    '| Section | Approved passages | Words |',
    '| --- | ---: | ---: |',
    ...analysis.sectionCoverage.map(
      (row) => `| ${row.section} | ${row.passageCount} | ${row.wordCount} |`,
    ),
    '',
    '## Notes',
    '',
    ...analysis.warnings.map((warning) => `- ${warning}`),
    '',
    workspace.documents.some((document) => document.synthetic)
      ? 'This project contains explicitly labeled synthetic demonstration documents.'
      : 'Source documents were supplied by the user; provenance is recorded in project.json.',
    '',
  ].join('\n')
}

/** Produce a self-contained ZIP with checksums for every included content file. */
export async function exportWorkspaceZip(workspace: Workspace): Promise<Blob> {
  const { default: JSZip } = await import('jszip')
  const zip = new JSZip()
  const files: Record<string, string> = {
    'project.json': JSON.stringify(workspace, null, 2),
    'manuscript.md': workspace.manuscript,
    'analysis.json': JSON.stringify(analyzeWorkspace(workspace), null, 2),
    'report.md': report(workspace),
    'README.md':
      '# ManuscriptForge project export\n\nOpen project.json with ManuscriptForge to restore this workspace. Original uploaded binary files are not retained; corpus/ contains their extracted text. Source hashes in project.json identify normalized extracted text, not original binary bytes. The manifest hashes the exact UTF-8 bytes of every listed export file. It does not list itself. Review decisions and notes are in curation.csv. Only approved passages contribute to the descriptive analysis.\n\nThis export may contain private source prose and review notes.\n',
  }
  const sourceNames = new Map(workspace.documents.map((document) => [document.id, document.name]))
  files['curation.csv'] = [
    [
      'passage_id',
      'document_id',
      'source_name',
      'section',
      'decision',
      'text_sha256',
      'review_note',
      'text',
    ]
      .map(csvCell)
      .join(','),
    ...workspace.passages.map((passage) =>
      [
        passage.id,
        passage.documentId,
        sourceNames.get(passage.documentId) ?? '',
        passage.section,
        passage.decision,
        passage.hash,
        passage.note,
        passage.text,
      ]
        .map(csvCell)
        .join(','),
    ),
  ].join('\r\n')
  workspace.documents.forEach((document, index) => {
    files[`corpus/${String(index + 1).padStart(3, '0')}-${safeFilename(document.name)}.txt`] =
      document.text
  })
  const manifest = await Promise.all(
    Object.entries(files).map(async ([path, text]) => ({
      path,
      bytes: new TextEncoder().encode(text).byteLength,
      sha256: await sha256(text),
    })),
  )
  Object.entries(files).forEach(([path, text]) => zip.file(path, text))
  zip.file(
    'manifest.json',
    JSON.stringify({ schemaVersion: 1, algorithm: 'SHA-256', files: manifest }, null, 2),
  )
  return zip.generateAsync({ type: 'blob', compression: 'DEFLATE' })
}

export async function exportManuscriptDocx(workspace: Workspace): Promise<Blob> {
  const { Document, HeadingLevel, Packer, Paragraph, TextRun } = await import('docx')
  const paragraphs = workspace.manuscript.split(/\n/).map((line) => {
    const heading = /^(#{1,3})\s+(.+)$/.exec(line)
    if (heading)
      return new Paragraph({
        text: heading[2],
        heading: [HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3][
          heading[1].length - 1
        ],
      })
    return new Paragraph({ children: [new TextRun(line)], spacing: { after: 160 } })
  })
  const document = new Document({
    title: workspace.name,
    description: 'User-authored manuscript exported from ManuscriptForge.',
    sections: [{ children: paragraphs }],
  })
  return Packer.toBlob(document)
}

export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = safeFilename(name)
  anchor.hidden = true
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  // Keep the URL alive long enough for browsers to start the download.
  window.setTimeout(() => URL.revokeObjectURL(url), 30000)
}
