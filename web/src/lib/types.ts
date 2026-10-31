export type Section =
  | 'abstract'
  | 'introduction'
  | 'methods'
  | 'results'
  | 'discussion'
  | 'limitations'
  | 'conclusion'
  | 'other'
export type Decision = 'pending' | 'approved' | 'excluded'

export interface SourceDocument {
  id: string
  name: string
  format: string
  text: string
  /** SHA-256 of normalized extracted text, not the original binary file. */
  hash: string
  addedAt: string
  synthetic: boolean
}

export interface Passage {
  id: string
  documentId: string
  section: Section
  text: string
  hash: string
  decision: Decision
  note: string
}

export interface Workspace {
  schemaVersion: 1
  id: string
  name: string
  description: string
  documents: SourceDocument[]
  passages: Passage[]
  manuscript: string
  createdAt: string
  updatedAt: string
}

export interface Analysis {
  approvedCount: number
  pendingCount: number
  excludedCount: number
  wordCount: number
  sentenceCount: number
  averageSentenceLength: number
  averageParagraphLength: number
  /** Unique lowercase tokens divided by all tokens (0–1); length dependent. */
  lexicalDiversity: number
  hedgesPer1000: number
  /** Rule-matched passive constructions per 1,000 words, not a validated parser. */
  passivePer1000: number
  sectionCoverage: { section: Section; passageCount: number; wordCount: number }[]
  topTerms: { term: string; count: number }[]
  sentenceLengths: number[]
  warnings: string[]
}
