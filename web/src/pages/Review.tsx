import { useMemo, useState } from 'react'
import {
  Check,
  X,
  Search,
  FileText,
  Info,
  Fingerprint,
  RotateCcw,
  MessageSquare,
  ArrowRight,
} from 'lucide-react'
import { Badge, Button, Empty, number, title, type PageProps } from '../ui'
import { countWords, SECTIONS } from '../lib/analysis'
import type { Decision, Passage, Section } from '../lib/types'
import './analysis-pages.css'

export default function Review({
  workspace,
  analysis,
  update,
  navigate,
  importDocuments,
}: PageProps) {
  const [query, setQuery] = useState('')
  const [decision, setDecision] = useState<Decision | 'all'>('all')
  const [section, setSection] = useState<Section | 'all'>('all')
  const [selectedId, setSelectedId] = useState<string>('')
  const documents = useMemo(
    () => new Map(workspace.documents.map((document) => [document.id, document])),
    [workspace.documents],
  )
  const filtered = useMemo(
    () =>
      workspace.passages.filter(
        (passage) =>
          (decision === 'all' || passage.decision === decision) &&
          (section === 'all' || passage.section === section) &&
          `${passage.text} ${documents.get(passage.documentId)?.name ?? ''} ${passage.note}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [workspace.passages, decision, section, query, documents],
  )
  const active = filtered.find((passage) => passage.id === selectedId) ?? filtered[0]
  const source = active ? documents.get(active.documentId) : undefined
  const duplicates = active
    ? workspace.passages.filter(
        (passage) => passage.hash === active.hash && passage.id !== active.id,
      )
    : []
  const edit = (id: string, values: Partial<Pick<Passage, 'decision' | 'note' | 'section'>>) =>
    update((current) => ({
      ...current,
      passages: current.passages.map((passage) =>
        passage.id === id ? { ...passage, ...values } : passage,
      ),
    }))

  return (
    <div className="analysis-page">
      <div className="review-summary" aria-label="Passage review totals">
        <span>
          <i className="review-dot approved" />
          {number(analysis.approvedCount)} approved
        </span>
        <span>
          <i className="review-dot pending" />
          {number(analysis.pendingCount)} awaiting review
        </span>
        <span>
          <i className="review-dot excluded" />
          {number(analysis.excludedCount)} excluded
        </span>
        <span className="review-summary-note">Only approved passages enter your profile</span>
        <button className="text-button" onClick={() => navigate('style')}>
          View profile <ArrowRight size={14} />
        </button>
      </div>
      {workspace.passages.length === 0 ? (
        <div className="panel">
          <Empty heading="Your review starts with a document">
            <p>
              Import a writing sample to extract passages and decide which ones belong in your
              corpus.
            </p>
            <Button onClick={importDocuments}>Add documents</Button>
          </Empty>
        </div>
      ) : (
        <>
          <div className="review-toolbar">
            <div className="review-search">
              <Search size={16} />
              <input
                aria-label="Search passages"
                placeholder="Search passages, sources, or notes…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            <div className="decision-filters" aria-label="Filter by decision">
              {(['all', 'pending', 'approved', 'excluded'] as const).map((value) => (
                <button
                  key={value}
                  aria-pressed={decision === value}
                  onClick={() => setDecision(value)}
                >
                  {value === 'all' ? 'All passages' : title(value)}
                </button>
              ))}
            </div>
            <select
              aria-label="Filter by manuscript section"
              value={section}
              onChange={(event) => setSection(event.target.value as Section | 'all')}
            >
              <option value="all">All sections</option>
              {SECTIONS.map((value) => (
                <option key={value} value={value}>
                  {title(value)}
                </option>
              ))}
            </select>
          </div>
          <div className="review-workbench">
            <aside className="passage-list panel" aria-label="Extracted passages">
              <div className="passage-list-heading">
                <span>EXTRACTED PASSAGES</span>
                <span>{filtered.length}</span>
              </div>
              {filtered.length ? (
                filtered.map((passage) => (
                  <button
                    key={passage.id}
                    className={`passage-list-item ${active?.id === passage.id ? 'selected' : ''}`}
                    onClick={() => setSelectedId(passage.id)}
                    aria-pressed={active?.id === passage.id}
                  >
                    <div className="passage-item-top">
                      <span className="passage-section">{title(passage.section)}</span>
                      <span className={`passage-decision ${passage.decision}`}>
                        <span className={`review-dot ${passage.decision}`} />
                        {title(passage.decision)}
                      </span>
                    </div>
                    <p>{passage.text}</p>
                    <div className="passage-item-bottom">
                      <span>
                        <FileText size={12} />
                        {documents.get(passage.documentId)?.name ?? 'Unknown source'}
                      </span>
                      <span>{number(countWords(passage.text))} words</span>
                    </div>
                  </button>
                ))
              ) : (
                <div className="passage-no-results">
                  <Search size={24} />
                  <p>No matching passages.</p>
                  <button
                    className="text-button"
                    onClick={() => {
                      setQuery('')
                      setDecision('all')
                      setSection('all')
                    }}
                  >
                    Clear filters
                  </button>
                </div>
              )}
            </aside>
            {active ? (
              <section className="passage-detail panel" aria-label="Selected passage">
                <div className="passage-detail-top">
                  <span className="eyebrow">
                    PASSAGE{' '}
                    {String(workspace.passages.findIndex((p) => p.id === active.id) + 1).padStart(
                      2,
                      '0',
                    )}
                  </span>
                  <Badge kind={active.decision}>{title(active.decision)}</Badge>
                </div>
                <div className="passage-detail-title">
                  <h2>{title(active.section)}</h2>
                  <span>{number(countWords(active.text))} words</span>
                </div>
                <div className="passage-source">
                  <FileText size={14} />
                  <span>{source?.name ?? 'Unknown source'}</span>
                  {source?.synthetic && <Badge>Synthetic example</Badge>}
                </div>
                <blockquote className="passage-prose">{active.text}</blockquote>
                {duplicates.length > 0 && (
                  <div className="duplicate-notice">
                    <Info size={17} />
                    <div>
                      <strong>Identical text appears elsewhere</strong>
                      <p>
                        This passage matches{' '}
                        {duplicates.length === 1 ? 'a passage' : `${duplicates.length} passages`} in{' '}
                        {Array.from(
                          new Set(
                            duplicates.map(
                              (p) => documents.get(p.documentId)?.name ?? 'another document',
                            ),
                          ),
                        ).join(', ')}
                        . Review duplicates to avoid weighting the same prose twice.
                      </p>
                    </div>
                  </div>
                )}
                <div className="passage-actions">
                  <Button
                    onClick={() => edit(active.id, { decision: 'approved' })}
                    variant={active.decision === 'approved' ? 'secondary' : 'primary'}
                  >
                    <Check size={16} />
                    {active.decision === 'approved' ? 'Approved' : 'Approve passage'}
                  </Button>
                  <Button
                    variant="secondary"
                    onClick={() => edit(active.id, { decision: 'excluded' })}
                  >
                    <X size={16} />
                    {active.decision === 'excluded' ? 'Excluded' : 'Exclude'}
                  </Button>
                  <button
                    className="review-reset"
                    onClick={() => edit(active.id, { decision: 'pending' })}
                    aria-label="Reset passage to pending"
                  >
                    <RotateCcw size={14} />
                    Reset
                  </button>
                </div>
                <div className="review-fields">
                  <label className="field-label">
                    Manuscript section
                    <select
                      aria-label="Change passage section"
                      value={active.section}
                      onChange={(event) =>
                        edit(active.id, { section: event.target.value as Section })
                      }
                    >
                      {SECTIONS.map((value) => (
                        <option key={value} value={value}>
                          {title(value)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-label">
                    <span>
                      <MessageSquare size={14} />
                      Review note
                    </span>
                    <textarea
                      aria-label="Passage review note"
                      rows={3}
                      placeholder="Record why this passage belongs, or why it does not…"
                      value={active.note}
                      onChange={(event) => edit(active.id, { note: event.target.value })}
                    />
                  </label>
                </div>
                <details className="passage-provenance">
                  <summary>
                    <Fingerprint size={15} />
                    Source & integrity details
                  </summary>
                  <dl>
                    <dt>Source document</dt>
                    <dd>{source?.name}</dd>
                    <dt>Passage SHA-256</dt>
                    <dd className="hash-value">{active.hash}</dd>
                    <dt>Extracted source SHA-256</dt>
                    <dd className="hash-value">{source?.hash}</dd>
                    <dt>What is hashed</dt>
                    <dd>
                      Normalized extracted text. These hashes identify content; they do not verify a
                      document’s authorship or scientific claims.
                    </dd>
                  </dl>
                </details>
              </section>
            ) : (
              <section className="panel passage-detail">
                <Empty heading="No passage selected">
                  Adjust your filters to return to your writing.
                </Empty>
              </section>
            )}
          </div>
        </>
      )}
    </div>
  )
}
