import type { Analysis, Passage, Section, SourceDocument, Workspace } from './types'
import { DEMO_PASSAGES } from './demoText'

export const SECTIONS: Section[] = [
  'abstract',
  'introduction',
  'methods',
  'results',
  'discussion',
  'limitations',
  'conclusion',
  'other',
]

const ALIASES: Record<string, Section | 'references'> = {
  abstract: 'abstract',
  summary: 'abstract',
  'structured abstract': 'abstract',
  introduction: 'introduction',
  background: 'introduction',
  rationale: 'introduction',
  methods: 'methods',
  methodology: 'methods',
  'materials and methods': 'methods',
  'materials methods': 'methods',
  'study design': 'methods',
  results: 'results',
  findings: 'results',
  discussion: 'discussion',
  interpretation: 'discussion',
  limitations: 'limitations',
  'study limitations': 'limitations',
  'limitations and future work': 'limitations',
  conclusion: 'conclusion',
  conclusions: 'conclusion',
  'concluding remarks': 'conclusion',
  references: 'references',
  bibliography: 'references',
  'reference list': 'references',
  'literature cited': 'references',
  'works cited': 'references',
}
const HEDGES = new Set([
  'may',
  'might',
  'could',
  'suggest',
  'suggests',
  'suggested',
  'consistent',
  'possible',
  'potential',
  'appears',
  'associated',
])
const STOPWORDS = new Set(
  (
    'a an and are as at be by for from in is of on or that the this to we with it its ' +
    'was were would will can could should may might not no each their these those than ' +
    'has have had but into only also they them our which such whether while without'
  ).split(/\s+/),
)

export function normalizeText(text: string): string {
  return text
    .replace(/^\uFEFF/, '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\t\u00A0]/g, ' ')
    .split('\n')
    .map((line) => line.trim().replace(/ {2,}/g, ' '))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** English-oriented tokenization, matching the Python core's word_tokens rule. */
export function wordTokens(text: string): string[] {
  return text.toLowerCase().match(/[a-z][a-z'-]*/g) ?? []
}

export function countWords(text: string): number {
  return wordTokens(text).length
}

/** Transparent punctuation heuristic, matching the Python core. */
export function splitSentences(text: string): string[] {
  const normalized = text.replace(/\s+/g, ' ').trim()
  return normalized ? normalized.split(/(?<=[.!?])\s+(?=[A-Z0-9])/).filter(Boolean) : []
}

export async function sha256(text: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))
  return Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function heading(line: string): Section | 'references' | null {
  const clean = line
    .replace(/^#{1,6}\s*/, '')
    .replace(/^\d+(?:\.\d+)*\.?\s+/, '')
    .replace(/^[*_]+|[*_]+$/g, '')
    .replace(/[\s:.-]+$/, '')
    .toLowerCase()
    .trim()
  return Object.hasOwn(ALIASES, clean) ? ALIASES[clean] : null
}

/** One passage per prose paragraph; references and everything after them are excluded. */
export function splitPassages(text: string): { section: Section; text: string }[] {
  const result: { section: Section; text: string }[] = []
  let section: Section = 'other'
  let buffer: string[] = []
  const flush = () => {
    const value = buffer.join('\n').trim()
    if (value) result.push({ section, text: value })
    buffer = []
  }
  for (const line of normalizeText(text).split('\n')) {
    const detected = heading(line)
    if (detected) {
      flush()
      if (detected === 'references') break
      section = detected
    } else if (!line) {
      flush()
    } else if (/^#{1,6}\s+/.test(line)) {
      // Unknown Markdown headings are structural labels, not sample prose.
      flush()
    } else {
      buffer.push(line)
    }
  }
  flush()
  return result
}

export async function createDocument(
  name: string,
  text: string,
  format = 'md',
  synthetic = false,
): Promise<{ document: SourceDocument; passages: Passage[] }> {
  const normalized = normalizeText(text)
  const document: SourceDocument = {
    id: crypto.randomUUID(),
    name,
    format,
    text: normalized,
    hash: await sha256(normalized),
    addedAt: new Date().toISOString(),
    synthetic,
  }
  const passages = await Promise.all(
    splitPassages(normalized).map(async (piece) => ({
      ...piece,
      id: crypto.randomUUID(),
      documentId: document.id,
      hash: await sha256(piece.text),
      decision: 'pending' as const,
      note: '',
    })),
  )
  return { document, passages }
}

export function createEmptyWorkspace(name = 'Untitled workspace'): Workspace {
  const now = new Date().toISOString()
  return {
    schemaVersion: 1,
    id: crypto.randomUUID(),
    name,
    description: '',
    documents: [],
    passages: [],
    manuscript: '',
    createdAt: now,
    updatedAt: now,
  }
}

export function analyzeWorkspace(workspace: Workspace): Analysis {
  const approved = workspace.passages.filter((passage) => passage.decision === 'approved')
  const text = approved.map((passage) => passage.text).join('\n\n')
  const tokens = wordTokens(text)
  const sentences = approved.flatMap((passage) => splitSentences(passage.text))
  const sentenceLengths = sentences.map(countWords)
  const paragraphCount = approved.reduce(
    (total, passage) => total + passage.text.split(/\n\s*\n/).filter((p) => p.trim()).length,
    0,
  )
  const wordCount = tokens.length
  const termCounts = new Map<string, number>()
  for (const token of tokens) {
    if (token.length > 2 && !STOPWORDS.has(token)) {
      termCounts.set(token, (termCounts.get(token) ?? 0) + 1)
    }
  }
  const round = (value: number) => Math.round(value * 100) / 100
  const pendingCount = workspace.passages.filter((passage) => passage.decision === 'pending').length
  const duplicateGroups = new Map<string, number>()
  for (const passage of workspace.passages) {
    duplicateGroups.set(passage.hash, (duplicateGroups.get(passage.hash) ?? 0) + 1)
  }
  const duplicates = [...duplicateGroups.values()].filter((count) => count > 1).length
  const approvedDuplicates = new Set<string>()
  const observed = new Set<string>()
  for (const passage of approved) {
    if (observed.has(passage.hash)) approvedDuplicates.add(passage.hash)
    observed.add(passage.hash)
  }
  const warnings: string[] = []
  if (!approved.length) warnings.push('Approve passages to build a descriptive profile.')
  if (pendingCount)
    warnings.push(
      `${pendingCount} passage${pendingCount === 1 ? '' : 's'} await review and are excluded from this profile.`,
    )
  if (duplicates)
    warnings.push(
      `${duplicates} identical passage group${duplicates === 1 ? '' : 's'} detected by normalized-text SHA-256.`,
    )
  if (approvedDuplicates.size)
    warnings.push(
      'Identical approved passages are counted separately; exclude repeats to avoid duplicate weighting.',
    )
  if (approved.length && wordCount < 1000)
    warnings.push(
      'This small corpus is suitable for exploration; its statistics do not establish a stable author style.',
    )
  warnings.push(
    'English-oriented token and sentence rules are descriptive heuristics, not measures of writing quality.',
  )
  return {
    approvedCount: approved.length,
    pendingCount,
    excludedCount: workspace.passages.filter((passage) => passage.decision === 'excluded').length,
    wordCount,
    sentenceCount: sentences.length,
    averageSentenceLength: sentences.length ? round(wordCount / sentences.length) : 0,
    averageParagraphLength: paragraphCount ? round(wordCount / paragraphCount) : 0,
    lexicalDiversity: wordCount ? round(new Set(tokens).size / wordCount) : 0,
    hedgesPer1000: wordCount
      ? round((tokens.filter((token) => HEDGES.has(token)).length / wordCount) * 1000)
      : 0,
    passivePer1000: wordCount
      ? round(
          ((text.match(/\b(?:was|were|is|are|been|be)\s+\w+(?:ed|en)\b/gi) ?? []).length /
            wordCount) *
            1000,
        )
      : 0,
    sectionCoverage: SECTIONS.map((section) => ({
      section,
      passageCount: approved.filter((passage) => passage.section === section).length,
      wordCount: approved
        .filter((passage) => passage.section === section)
        .reduce((sum, passage) => sum + countWords(passage.text), 0),
    })),
    topTerms: [...termCounts]
      .map(([term, count]) => ({ term, count }))
      .sort((a, b) => b.count - a.count || a.term.localeCompare(b.term))
      .slice(0, 16),
    sentenceLengths,
    warnings,
  }
}

export async function createDemoWorkspace(): Promise<Workspace> {
  const workspace = createEmptyWorkspace('The traceable writing project')
  workspace.description =
    'Explore an original synthetic corpus: three documents, six manuscript sections, and one deliberately repeated passage. No measurements or external citations.'
  const sectionText = (sections: (keyof typeof DEMO_PASSAGES)[], markdown = true) =>
    sections
      .map(
        (section) =>
          `${markdown ? '## ' : ''}${section[0].toUpperCase()}${section.slice(1)}\n\n${DEMO_PASSAGES[section]}`,
      )
      .join('\n\n')
  const sources = await Promise.all([
    createDocument(
      'protocol.md',
      sectionText(['abstract', 'introduction', 'methods']) +
        '\n\n## References\n\nSynthetic reference-section sentinel: no external sources were used.',
      'md',
      true,
    ),
    createDocument(
      'run_note.txt',
      sectionText(['results', 'discussion', 'limitations'], false),
      'txt',
      true,
    ),
    createDocument('methods_duplicate.docx', sectionText(['methods']), 'docx', true),
  ])
  workspace.documents = sources.map((source) => source.document)
  workspace.passages = sources.flatMap((source, index) =>
    source.passages.map((passage) => ({
      ...passage,
      decision: index === 2 ? ('excluded' as const) : ('approved' as const),
      note:
        index === 2
          ? 'Identical to the methods passage in protocol.md; excluded to avoid duplicate weighting.'
          : 'Approved synthetic demonstration passage.',
    })),
  )
  workspace.manuscript =
    '# Traceable documentation for a simulated distance-sensor workflow\n\n' +
    '> Synthetic demonstration. These passages describe an invented protocol, not measured results.\n\n' +
    sectionText(['abstract', 'introduction', 'methods', 'results', 'discussion', 'limitations'])
  return workspace
}
