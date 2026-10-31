import { describe, expect, it } from 'vitest'
import { createDemoWorkspace } from './analysis'
import {
  csvCell,
  exportManuscriptDocx,
  exportWorkspaceZip,
  parseWorkspace,
  readDocumentFile,
  safeFilename,
  verifyWorkspaceHashes,
} from './files'

describe('portable project validation', () => {
  it('round trips the demonstration including review notes and source linkage', async () => {
    const workspace = await createDemoWorkspace()
    expect(parseWorkspace(JSON.stringify(workspace))).toEqual(workspace)
    await expect(verifyWorkspaceHashes(workspace)).resolves.toBeUndefined()
  })

  it('rejects changed source text, changed passages, and falsely reassigned source links', async () => {
    const changedSource = await createDemoWorkspace()
    changedSource.documents[0].text += '\nThis was not in the original source.'
    await expect(verifyWorkspaceHashes(changedSource)).rejects.toThrow('does not match its text')
    const changedPassage = await createDemoWorkspace()
    changedPassage.passages[0].text += ' Added words.'
    await expect(verifyWorkspaceHashes(changedPassage)).rejects.toThrow(
      'without updating its fingerprint',
    )
    const changedLink = await createDemoWorkspace()
    changedLink.passages[0].documentId = changedLink.documents[1].id
    await expect(verifyWorkspaceHashes(changedLink)).rejects.toThrow('cannot be traced')
  })

  it('rejects invalid JSON, unsupported versions, duplicate IDs, and orphaned passages', async () => {
    const workspace = await createDemoWorkspace()
    expect(() => parseWorkspace('{oops')).toThrow('not valid JSON')
    expect(() => parseWorkspace(JSON.stringify({ ...workspace, schemaVersion: 99 }))).toThrow(
      'Unsupported project version',
    )
    expect(() =>
      parseWorkspace(
        JSON.stringify({
          ...workspace,
          documents: [...workspace.documents, workspace.documents[0]],
        }),
      ),
    ).toThrow('document IDs must be unique')
    expect(() =>
      parseWorkspace(
        JSON.stringify({ ...workspace, passages: [...workspace.passages, workspace.passages[0]] }),
      ),
    ).toThrow('passage IDs must be unique')
    const orphan = { ...workspace.passages[0], documentId: 'missing-source' }
    expect(() => parseWorkspace(JSON.stringify({ ...workspace, passages: [orphan] }))).toThrow(
      'missing source document',
    )
  })

  it('rejects invalid statuses, dates, hashes, and unbounded text fields', async () => {
    const workspace = await createDemoWorkspace()
    for (const change of [
      { decision: 'accepted' },
      { section: 'hacked' },
      { hash: 'not-a-hash' },
      { note: 'a'.repeat(10001) },
    ]) {
      expect(() =>
        parseWorkspace(
          JSON.stringify({ ...workspace, passages: [{ ...workspace.passages[0], ...change }] }),
        ),
      ).toThrow('Invalid project')
    }
    expect(() => parseWorkspace(JSON.stringify({ ...workspace, createdAt: 'bad date' }))).toThrow(
      'timestamp',
    )
    expect(() => parseWorkspace(JSON.stringify({ ...workspace, name: '' }))).toThrow('nonempty')
  })

  it('discards arbitrary imported properties rather than merging them into application state', async () => {
    const workspace = await createDemoWorkspace()
    const input = JSON.stringify({ ...workspace, executable: '<script>alert(1)</script>' }).replace(
      '"schemaVersion":1',
      '"schemaVersion":1,"__proto__":{"polluted":true}',
    )
    const result = parseWorkspace(input)
    expect(Object.hasOwn(result, '__proto__')).toBe(false)
    expect(Object.hasOwn(result, 'executable')).toBe(false)
    expect(result).toEqual(workspace)
  })
})

describe('export safety and completeness', () => {
  it('sanitizes file basenames and spreadsheet formulas', () => {
    expect(safeFilename('../../evil\\file:name.txt')).not.toMatch(/[\\/:]/)
    expect(safeFilename('CON.txt')).toBe('file-CON.txt')
    expect(safeFilename('...')).toBe('untitled')
    expect(csvCell('=HYPERLINK("evil")')).toBe('"\'=HYPERLINK(""evil"")"')
    expect(csvCell(' +SUM(A1:A2)')).toBe('"\' +SUM(A1:A2)"')
    expect(csvCell('plain, text')).toBe('"plain, text"')
  })

  it('exports a restorable ZIP with matching UTF-8 sizes and SHA-256 checksums', async () => {
    const { default: JSZip } = await import('jszip')
    const workspace = await createDemoWorkspace()
    workspace.documents[0].name = '../../dangerous.md'
    workspace.passages[0].note = '=HYPERLINK("bad")'
    const blob = await exportWorkspaceZip(workspace)
    const zip = await JSZip.loadAsync(await blob.arrayBuffer())
    const manifest = JSON.parse(await zip.file('manifest.json')!.async('string')) as {
      files: { path: string; bytes: number; sha256: string }[]
    }
    expect(manifest.files.map((item) => item.path)).toEqual(
      expect.arrayContaining([
        'project.json',
        'manuscript.md',
        'curation.csv',
        'analysis.json',
        'report.md',
        'README.md',
      ]),
    )
    expect(manifest.files.filter((item) => item.path.startsWith('corpus/'))).toHaveLength(3)
    expect(Object.keys(zip.files).some((path) => path.includes('../'))).toBe(false)
    expect(parseWorkspace(await zip.file('project.json')!.async('string'))).toEqual(workspace)
    for (const item of manifest.files) {
      const bytes = await zip.file(item.path)!.async('uint8array')
      expect(bytes.byteLength).toBe(item.bytes)
      const digest = await crypto.subtle.digest('SHA-256', new Uint8Array(bytes))
      expect(
        [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, '0')).join(''),
      ).toBe(item.sha256)
    }
    expect(await zip.file('curation.csv')!.async('string')).toContain("'=HYPERLINK")
  })

  it('creates an actual DOCX archive with manuscript text and headings', async () => {
    const { default: JSZip } = await import('jszip')
    const workspace = await createDemoWorkspace()
    workspace.manuscript = '# A draft title\n\n## Methods\nA user-written paragraph.'
    const blob = await exportManuscriptDocx(workspace)
    const zip = await JSZip.loadAsync(await blob.arrayBuffer())
    const xml = await zip.file('word/document.xml')!.async('string')
    expect(xml).toContain('A draft title')
    expect(xml).toContain('A user-written paragraph.')
    expect(xml).toContain('Heading2')
  })
})

describe('document import', () => {
  it('accepts UTF-8 text while rejecting unsupported files, oversized files, and empty text', async () => {
    const text = new File(['## Methods\n\nA procedure.'], 'methods.md', { type: 'text/markdown' })
    expect(await readDocumentFile(text)).toEqual({
      name: 'methods.md',
      format: 'md',
      text: '## Methods\n\nA procedure.',
    })
    await expect(readDocumentFile(new File([''], 'empty.txt'))).rejects.toThrow('no readable text')
    await expect(readDocumentFile(new File(['x'], 'program.exe'))).rejects.toThrow(
      'choose a Markdown',
    )
    const oversized = { name: 'large.txt', size: 10 * 1024 * 1024 + 1 } as File
    await expect(readDocumentFile(oversized)).rejects.toThrow('10 MB')
    await expect(
      readDocumentFile(new File([new Uint8Array([0xff])], 'invalid.txt')),
    ).rejects.toThrow('UTF-8')
  })
})
