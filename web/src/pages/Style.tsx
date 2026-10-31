import { useMemo, useState } from 'react'
import {
  ArrowRight,
  Info,
  FileText,
  AlignLeft,
  Quote,
  Fingerprint,
  ArrowUpRight,
} from 'lucide-react'
import { Badge, Button, Empty, number, title, type PageProps } from '../ui'
import { analyzeWorkspace, countWords, SECTIONS } from '../lib/analysis'
import type { Section } from '../lib/types'
import './analysis-pages.css'

const BINS = [
  { label: '1–10', min: 1, max: 10 },
  { label: '11–20', min: 11, max: 20 },
  { label: '21–30', min: 21, max: 30 },
  { label: '31–40', min: 31, max: 40 },
  { label: '41–50', min: 41, max: 50 },
  { label: '51+', min: 51, max: Infinity },
]

export default function Style({ workspace, analysis, navigate }: PageProps) {
  const [scope, setScope] = useState<Section | 'all'>('all')
  const metrics = useMemo(
    () =>
      scope === 'all'
        ? analysis
        : analyzeWorkspace({
            ...workspace,
            passages: workspace.passages.filter((passage) => passage.section === scope),
          }),
    [workspace, analysis, scope],
  )
  const bins = BINS.map((bin) => ({
    ...bin,
    count: metrics.sentenceLengths.filter((length) => length >= bin.min && length <= bin.max)
      .length,
  }))
  const maxBin = Math.max(1, ...bins.map((bin) => bin.count))
  const maxTerm = Math.max(1, ...metrics.topTerms.map((term) => term.count))
  const approved = workspace.passages.filter(
    (passage) => passage.decision === 'approved' && (scope === 'all' || passage.section === scope),
  )
  const documents = new Map(workspace.documents.map((document) => [document.id, document]))
  const represented = analysis.sectionCoverage.filter((item) => item.passageCount > 0).length
  return (
    <div className="analysis-page">
      <div className="style-scope" aria-label="Style profile section">
        {(['all', ...SECTIONS] as const).map((section) => (
          <button key={section} aria-pressed={scope === section} onClick={() => setScope(section)}>
            {section === 'all' ? 'Whole corpus' : title(section)}
            {section !== 'all' && (
              <span>
                {analysis.sectionCoverage.find((value) => value.section === section)
                  ?.passageCount ?? 0}
              </span>
            )}
          </button>
        ))}
      </div>
      {!metrics.approvedCount ? (
        <div className="panel">
          <Empty
            heading={
              scope === 'all'
                ? 'A profile begins with your decisions'
                : `No approved ${scope} passages yet`
            }
          >
            <p>
              Approve a passage in this section to explore its sentence patterns, vocabulary, and
              source examples.
            </p>
            <Button onClick={() => navigate('review')}>
              Review passages <ArrowRight size={15} />
            </Button>
          </Empty>
        </div>
      ) : (
        <>
          <div className="stats-grid style-stats">
            <div className="stat-card">
              <div className="stat-label">
                <FileText size={15} />
                Approved words
              </div>
              <div className="stat-number">{number(metrics.wordCount)}</div>
              <div className="stat-foot">Across {metrics.approvedCount} reviewed passages</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">
                <AlignLeft size={15} />
                Sentence length
              </div>
              <div className="stat-number">
                {number(metrics.averageSentenceLength)}
                <small> words</small>
              </div>
              <div className="stat-foot">Mean across {metrics.sentenceCount} sentences</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">
                <Quote size={15} />
                Paragraph length
              </div>
              <div className="stat-number">
                {number(metrics.averageParagraphLength)}
                <small> words</small>
              </div>
              <div className="stat-foot">Mean approved paragraph length</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">
                <Fingerprint size={15} />
                Vocabulary variety
              </div>
              <div className="stat-number">
                {number(metrics.lexicalDiversity * 100)}
                <small>%</small>
              </div>
              <div className="stat-foot">Unique tokens · length dependent</div>
            </div>
          </div>
          <div className="style-charts">
            <section className="panel distribution-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">THE RHYTHM OF YOUR WRITING</p>
                  <h2>Sentence distribution</h2>
                </div>
                <Badge>{metrics.sentenceCount} sentences</Badge>
              </div>
              <p className="chart-explainer">How much space each thought takes.</p>
              <div
                className="histogram"
                role="img"
                aria-label={`Sentence length histogram: ${bins.map((bin) => `${bin.label} words: ${bin.count} sentences`).join('; ')}`}
              >
                <div className="histogram-plot">
                  {bins.map((bin) => (
                    <div className="histogram-column" key={bin.label}>
                      <span className="histogram-count">{bin.count || ''}</span>
                      <div
                        className={`histogram-bar ${bin.count ? '' : 'empty-bar'}`}
                        style={{
                          height: `${Math.max(bin.count ? 4 : 1, (bin.count / maxBin) * 100)}%`,
                        }}
                      />
                      <span className="histogram-label">{bin.label}</span>
                    </div>
                  ))}
                </div>
                <span className="histogram-axis">WORDS PER SENTENCE</span>
              </div>
              <div className="chart-note">
                <Info size={14} />
                Sentence boundaries use punctuation heuristics.
              </div>
            </section>
            <section className="panel terms-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">RECURRING LANGUAGE</p>
                  <h2>Words you return to</h2>
                </div>
                <Badge>Top 8 terms</Badge>
              </div>
              <p className="chart-explainer">
                Frequency after common English stop words are removed.
              </p>
              <div className="term-list">
                {metrics.topTerms.slice(0, 8).map(({ term, count }) => (
                  <div className="term-row" key={term}>
                    <span>{term}</span>
                    <div className="term-track">
                      <div style={{ width: `${(count / maxTerm) * 100}%` }} />
                    </div>
                    <strong>{count}</strong>
                  </div>
                ))}
              </div>
            </section>
          </div>
          <div className="style-details-grid">
            <section className="panel indicators-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">SMALL SIGNALS</p>
                  <h2>Language indicators</h2>
                </div>
              </div>
              <div className="indicator-row">
                <div>
                  <strong>Hedging language</strong>
                  <p>Rule-matched terms such as may, could, and suggests.</p>
                </div>
                <span>
                  {number(metrics.hedgesPer1000)}
                  <small>per 1,000 words</small>
                </span>
              </div>
              <div className="indicator-row">
                <div>
                  <strong>Passive constructions</strong>
                  <p>Simple patterns such as “was recorded.”</p>
                </div>
                <span>
                  {number(metrics.passivePer1000)}
                  <small>per 1,000 words</small>
                </span>
              </div>
              <div className="style-indicator-note">
                These indicators describe language patterns. They do not score clarity, quality, or
                scientific validity.
              </div>
            </section>
            <section className="panel coverage-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">A BALANCED VIEW</p>
                  <h2>Section coverage</h2>
                </div>
                <Badge>
                  {represented} / {SECTIONS.length}
                </Badge>
              </div>
              <div className="style-coverage-list">
                {analysis.sectionCoverage.map((item) => (
                  <button
                    key={item.section}
                    onClick={() => setScope(item.section)}
                    className={scope === item.section ? 'active' : ''}
                  >
                    <span>{title(item.section)}</span>
                    <span className="coverage-line">
                      <i
                        style={{
                          width: `${Math.min(100, (item.passageCount / Math.max(1, ...analysis.sectionCoverage.map((entry) => entry.passageCount))) * 100)}%`,
                        }}
                      />
                    </span>
                    <span>{item.passageCount}</span>
                  </button>
                ))}
              </div>
              <p className="chart-note">Passage counts show representation, not writing quality.</p>
            </section>
          </div>
          <section className="panel style-examples">
            <div className="panel-heading">
              <div>
                <p className="eyebrow">ALWAYS TRACEABLE</p>
                <h2>From profile back to prose</h2>
              </div>
              <button className="text-button" onClick={() => navigate('review')}>
                Review all <ArrowUpRight size={15} />
              </button>
            </div>
            <div className="style-example-grid">
              {approved.slice(0, 2).map((passage) => (
                <article key={passage.id}>
                  <div>
                    <Badge>{title(passage.section)}</Badge>
                    <span>{number(countWords(passage.text))} words</span>
                  </div>
                  <p>“{passage.text}”</p>
                  <footer>
                    <FileText size={13} />
                    {documents.get(passage.documentId)?.name}
                  </footer>
                </article>
              ))}
            </div>
          </section>
          <details className="style-methods panel">
            <summary>
              <Info size={16} />
              How to read this profile
            </summary>
            <p>
              Only approved passages contribute to these statistics. English-oriented tokenization
              matches the Python core’s word rule; hyphenated words remain one token, and standalone
              numbers are excluded. A sentence ends at punctuation followed by a capital letter or
              number. Vocabulary variety is a type–token ratio and changes with corpus length. These
              are transparent descriptive rules, not a trained author model.
            </p>
            <ul>
              {metrics.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </details>
        </>
      )}
    </div>
  )
}
