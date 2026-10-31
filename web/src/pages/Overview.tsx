import {
  ArrowRight,
  ArrowUpRight,
  Check,
  FileText,
  Layers3,
  Library,
  ListChecks,
  Plus,
  Sparkles,
} from 'lucide-react'
import { Badge, Button, number, title, type PageProps } from '../ui'

export default function Overview({
  workspace,
  analysis,
  navigate,
  importDocuments,
  newProject,
}: PageProps & { newProject: () => void }) {
  const covered = analysis.sectionCoverage.filter((section) => section.passageCount > 0).length
  const maximum = Math.max(1, ...analysis.sectionCoverage.map((section) => section.wordCount))
  const synthetic = workspace.documents.some((document) => document.synthetic)
  const steps = [
    {
      icon: Library,
      heading: 'Bring your sources together',
      description: `${workspace.documents.length} documents in your collection`,
      complete: workspace.documents.length > 0,
      page: 'corpus' as const,
    },
    {
      icon: ListChecks,
      heading: 'Choose the passages that matter',
      description: analysis.pendingCount
        ? `${analysis.pendingCount} passages waiting for your review`
        : `${analysis.approvedCount} approved · ${analysis.excludedCount} excluded`,
      complete: analysis.pendingCount === 0 && analysis.approvedCount > 0,
      page: 'review' as const,
    },
    {
      icon: Sparkles,
      heading: 'Explore your writing patterns',
      description: analysis.approvedCount
        ? `${covered} sections represented in your profile`
        : 'Approve passages to build your first profile',
      complete: false,
      page: 'style' as const,
    },
  ]
  return (
    <div className="overview-page">
      <section className="hero-panel">
        <div className="hero-copy">
          <div className="eyebrow">A LITTLE STRUCTURE. MORE ROOM TO THINK.</div>
          <h1>
            Your writing.
            <br />
            <em>In context.</em>
          </h1>
          <p>
            From the first source to the final draft, keep your writing connected to the decisions
            behind it.
          </p>
          <div className="hero-actions">
            <Button onClick={() => navigate(analysis.pendingCount ? 'review' : 'style')}>
              {analysis.pendingCount ? 'Review your passages' : 'Explore your style'}{' '}
              <ArrowRight size={16} />
            </Button>
            <Button variant="ghost" onClick={importDocuments}>
              <Plus size={15} /> Add documents
            </Button>
          </div>
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="paper paper-back" />
          <div className="paper paper-front">
            <div className="paper-topline">
              <span>MANUSCRIPTFORGE</span>
              <span>RESEARCH NOTES</span>
            </div>
            <h3>
              A traceable
              <br />
              writing process.
            </h3>
            <div className="paper-line" />
            <div className="paper-line long" />
            <div className="paper-line" />
            <div className="paper-line short" />
            <div className="paper-annotation">
              <Check size={12} />
              <span>Source connected</span>
            </div>
            <div className="paper-line long" />
            <div className="paper-line" />
            <div className="paper-line short" />
            <div className="paper-footnote">COLLECT · CURATE · UNDERSTAND</div>
          </div>
          <div className="art-orbit art-orbit-one" />
          <div className="art-orbit art-orbit-two" />
        </div>
      </section>
      <div className="stats-grid">
        {[
          {
            label: 'Source documents',
            value: workspace.documents.length,
            detail: 'Retained with text fingerprints',
            icon: FileText,
          },
          {
            label: 'Extracted passages',
            value: workspace.passages.length,
            detail: `${analysis.pendingCount} awaiting review`,
            icon: Layers3,
          },
          {
            label: 'Words in your profile',
            value: analysis.wordCount,
            detail: 'From approved passages only',
            icon: ListChecks,
          },
          {
            label: 'Sections represented',
            value: covered,
            detail: 'Across your approved collection',
            icon: Library,
          },
        ].map((stat) => (
          <div className="stat-card" key={stat.label}>
            <div className="stat-label">
              <span>{stat.label}</span>
              <stat.icon size={16} strokeWidth={1.5} />
            </div>
            <div className="stat-number">{number(stat.value)}</div>
            <p>{stat.detail}</p>
          </div>
        ))}
      </div>
      <div className="overview-grid">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <h2>The shape of your collection</h2>
              <p>Approved words by manuscript section</p>
            </div>
            <button className="text-button" onClick={() => navigate('style')}>
              View profile <ArrowUpRight size={14} />
            </button>
          </div>
          <div
            className="coverage-chart"
            role="img"
            aria-label={`Approved words by section: ${analysis.sectionCoverage.map((section) => `${title(section.section)} ${section.wordCount}`).join(', ')}`}
          >
            {analysis.sectionCoverage.map((section) => (
              <div className="chart-column" key={section.section}>
                <span className="chart-value">{section.wordCount || '—'}</span>
                <div className="bar-track">
                  <div
                    className={`chart-bar ${section.section === 'methods' ? 'accent' : ''}`}
                    style={{
                      height: section.wordCount
                        ? `${Math.max(4, (section.wordCount / maximum) * 100)}%`
                        : '2px',
                    }}
                  />
                </div>
                <span className="chart-label">{title(section.section)}</span>
              </div>
            ))}
          </div>
          <div className="chart-note">
            <span className="status-dot" /> Section coverage describes quantity, not writing
            quality.
          </div>
        </section>
        <section className="panel workflow-panel">
          <div className="panel-heading">
            <div>
              <h2>A clear next step</h2>
              <p>Build a profile you can trace back.</p>
            </div>
          </div>
          <div className="workflow-steps">
            {steps.map((step, index) => (
              <button
                className={`workflow-step ${step.complete ? 'complete' : ''}`}
                key={step.heading}
                onClick={() => navigate(step.page)}
              >
                <span className="step-icon">
                  {step.complete ? <Check size={16} /> : <step.icon size={16} />}
                </span>
                <span className="step-copy">
                  <small>0{index + 1}</small>
                  <strong>{step.heading}</strong>
                  <span>{step.description}</span>
                </span>
                <ArrowUpRight size={15} />
              </button>
            ))}
          </div>
          <div className="workflow-footer">
            <Badge kind="success">Local by design</Badge>
            <span>No account. No API key.</span>
          </div>
        </section>
      </div>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>At the source</h2>
            <p>Every passage begins with a document.</p>
          </div>
          <button className="text-button" onClick={() => navigate('corpus')}>
            Open library <ArrowUpRight size={14} />
          </button>
        </div>
        {workspace.documents.length ? (
          <div className="files-table-scroll">
            <table className="source-table">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Format</th>
                  <th>Passages</th>
                  <th>Origin</th>
                </tr>
              </thead>
              <tbody>
                {workspace.documents.slice(0, 3).map((document) => (
                  <tr key={document.id}>
                    <td>
                      <button className="document-link" onClick={() => navigate('corpus')}>
                        <span className="file-icon">
                          <FileText size={18} strokeWidth={1.5} />
                        </span>
                        {document.name}
                      </button>
                    </td>
                    <td>
                      <span className="format-label">{document.format.toUpperCase()}</span>
                    </td>
                    <td>
                      {
                        workspace.passages.filter((passage) => passage.documentId === document.id)
                          .length
                      }
                    </td>
                    <td>
                      <Badge>{document.synthetic ? 'Synthetic demo' : 'Your document'}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="overview-source-empty">
            <FileText size={22} />
            <p>Add your first writing sample to begin.</p>
            <Button variant="secondary" onClick={importDocuments}>
              <Plus size={14} /> Import document
            </Button>
          </div>
        )}
      </section>
      {synthetic && (
        <div className="demo-note">
          <div>
            <strong>A working example, ready to explore.</strong>
            <p>
              This collection uses original synthetic text about an imagined sensor workflow. It
              contains no experimental findings. Start a project to use your own writing.
            </p>
          </div>
          <Button variant="secondary" onClick={newProject}>
            Start your project <ArrowRight size={15} />
          </Button>
        </div>
      )}
    </div>
  )
}
