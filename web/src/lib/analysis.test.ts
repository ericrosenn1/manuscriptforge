import { describe, expect, it } from 'vitest'
import {
  analyzeWorkspace,
  countWords,
  createDemoWorkspace,
  createDocument,
  createEmptyWorkspace,
  normalizeText,
  sha256,
  splitPassages,
  splitSentences,
} from './analysis'

describe('source extraction and paragraph boundaries', () => {
  it('recognizes Markdown, numbered, and plain headings without including bibliography', () => {
    expect(
      splitPassages(
        [
          '# Study title',
          '## 1. Introduction',
          'A first paragraph.',
          '',
          'Another paragraph.',
          '',
          '2. Materials and methods',
          'A procedure.',
          '',
          'Bibliography',
          'Do not profile this reference.',
          '',
          '## Discussion',
          'Do not resume after bibliography.',
        ].join('\n'),
      ),
    ).toEqual([
      { section: 'introduction', text: 'A first paragraph.' },
      { section: 'introduction', text: 'Another paragraph.' },
      { section: 'methods', text: 'A procedure.' },
    ])
  })

  it('keeps references mentioned in prose and treats prototype names as text', () => {
    expect(splitPassages('constructor\n\nThis references a known procedure.')).toEqual([
      { section: 'other', text: 'constructor' },
      { section: 'other', text: 'This references a known procedure.' },
    ])
  })

  it('normalizes line endings and marks every imported passage pending', async () => {
    const first = await createDocument('source.md', '## Methods\r\n\r\nThe  method was checked.')
    const second = await createDocument('copy.md', '## Methods\n\nThe method was checked.')
    expect(first.document.hash).toEqual(second.document.hash)
    expect(first.passages[0].hash).toEqual(second.passages[0].hash)
    expect(first.document.id).not.toBe(second.document.id)
    expect(first.passages[0].decision).toBe('pending')
    expect(first.passages[0].section).toBe('methods')
    expect(first.document.synthetic).toBe(false)
  })

  it('hashes actual content with SHA-256 and records changes', async () => {
    expect(await sha256('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    )
    expect(await sha256('abc')).not.toBe(await sha256('abcd'))
    expect(normalizeText('\uFEFF line  one\r\n\r\n\r\nline\ttwo ')).toBe('line one\n\nline two')
  })
})

describe('curated descriptive analysis', () => {
  it('matches the release fixture and excludes its repeated methods passage', async () => {
    const demo = await createDemoWorkspace()
    const analysis = analyzeWorkspace(demo)
    expect(demo.documents).toHaveLength(3)
    expect(demo.documents.every((document) => document.synthetic)).toBe(true)
    expect(demo.passages).toHaveLength(7)
    expect(analysis.approvedCount).toBe(6)
    expect(analysis.excludedCount).toBe(1)
    expect(analysis.pendingCount).toBe(0)
    expect(analysis.wordCount).toBe(432)
    expect(analysis.sectionCoverage.filter((section) => section.passageCount > 0)).toHaveLength(6)
    const methods = demo.passages.filter((passage) => passage.section === 'methods')
    expect(methods[0].hash).toBe(methods[1].hash)
    expect(methods[0].documentId).not.toBe(methods[1].documentId)
    expect(demo.passages.some((passage) => passage.text.includes('sentinel'))).toBe(false)
    expect(analysis.warnings.some((warning) => warning.includes('identical passage group'))).toBe(
      true,
    )
  })

  it('recomputes all metrics from approved passages only, without mutating source records', async () => {
    const workspace = createEmptyWorkspace('Review workflow')
    const source = await createDocument(
      'methods.md',
      '## Methods\n\nWe may compare the method. A record was preserved.',
    )
    workspace.documents.push(source.document)
    workspace.passages.push(...source.passages)
    expect(analyzeWorkspace(workspace).wordCount).toBe(0)
    workspace.passages[0].decision = 'approved'
    const before = JSON.stringify(workspace)
    const metrics = analyzeWorkspace(workspace)
    expect(metrics.wordCount).toBe(9)
    expect(metrics.sentenceCount).toBe(2)
    expect(metrics.averageSentenceLength).toBe(4.5)
    expect(metrics.hedgesPer1000).toBe(111.11)
    expect(metrics.passivePer1000).toBe(111.11)
    expect(JSON.stringify(workspace)).toBe(before)
    workspace.passages[0].decision = 'excluded'
    expect(analyzeWorkspace(workspace).wordCount).toBe(0)
    expect(workspace.documents[0].text).toBe(source.document.text)
  })

  it('warns when both copies are approved instead of silently deduplicating', async () => {
    const demo = await createDemoWorkspace()
    demo.passages.find((passage) => passage.decision === 'excluded')!.decision = 'approved'
    const metrics = analyzeWorkspace(demo)
    expect(metrics.approvedCount).toBe(7)
    expect(metrics.wordCount).toBeGreaterThan(432)
    expect(metrics.warnings.some((warning) => warning.includes('counted separately'))).toBe(true)
  })

  it('returns finite empty metrics and never presents a quality score', () => {
    const metrics = analyzeWorkspace(createEmptyWorkspace())
    for (const value of Object.values(metrics)) {
      if (typeof value === 'number') expect(Number.isFinite(value)).toBe(true)
    }
    expect(metrics.wordCount).toBe(0)
    expect(metrics.topTerms).toEqual([])
    expect(metrics).not.toHaveProperty('qualityScore')
    expect(metrics.warnings[0]).toMatch(/Approve passages/)
  })

  it('filters stop words from top terms and exposes transparent token rules', async () => {
    expect(countWords('The first-person example contains 42 values.')).toBe(5)
    expect(splitSentences('A first sentence. Another sentence! Third?')).toHaveLength(3)
    const workspace = createEmptyWorkspace()
    const source = await createDocument('terms.md', 'The record is the record of a procedure.')
    source.passages[0].decision = 'approved'
    workspace.documents.push(source.document)
    workspace.passages.push(...source.passages)
    expect(analyzeWorkspace(workspace).topTerms[0]).toEqual({ term: 'record', count: 2 })
    expect(analyzeWorkspace(workspace).topTerms.some(({ term }) => term === 'the')).toBe(false)
  })
})
