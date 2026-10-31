import {
  Archive,
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  FileJson,
  FileText,
  FolderInput,
  ShieldCheck,
} from 'lucide-react'
import { Button, type ExportKind, type PageProps } from '../ui'
import './files-pages.css'

export default function Exports({
  workspace,
  analysis,
  download,
  restoreProject,
  navigate,
  busy,
}: PageProps) {
  const exports: {
    kind: ExportKind
    title: string
    extension: string
    description: string
    icon: typeof FileText
    disabled?: boolean
  }[] = [
    {
      kind: 'docx',
      title: 'Word document',
      extension: '.docx',
      description: 'Your manuscript with headings, ready to continue editing in Word.',
      icon: FileText,
      disabled: !workspace.manuscript.trim(),
    },
    {
      kind: 'md',
      title: 'Markdown manuscript',
      extension: '.md',
      description: 'An editable, portable copy of your manuscript text.',
      icon: FileText,
      disabled: !workspace.manuscript.trim(),
    },
    {
      kind: 'json',
      title: 'Project backup',
      extension: '.json',
      description:
        'Sources, decisions, notes, and draft. Restore here to pick up where you left off.',
      icon: FileJson,
    },
  ]
  return (
    <div className="files-page">
      <section className="export-feature panel">
        <div className="export-feature-main">
          <div className="export-archive-icon">
            <Archive size={31} strokeWidth={1.25} />
          </div>
          <div>
            <div className="eyebrow">THE COMPLETE PACKAGE</div>
            <h2>Your workspace. Fully traceable.</h2>
            <p>
              Download one ZIP with your manuscript, extracted sources, review decisions, and
              descriptive analysis. A SHA-256 manifest records the exact contents.
            </p>
            <Button onClick={() => void download('zip')} disabled={busy}>
              <ArrowDownToLine size={16} /> Download workspace{' '}
              <span className="button-filetype">ZIP</span>
            </Button>
          </div>
        </div>
        <div className="export-inventory">
          <span className="eyebrow">INSIDE THE ARCHIVE</span>
          {[
            ['project.json', 'Restorable project state'],
            ['manuscript.md', 'Your current draft'],
            ['curation.csv', 'Passage decisions and notes'],
            ['analysis.json + report.md', 'Approved-corpus statistics'],
            ['corpus/', `${workspace.documents.length} extracted source texts`],
            ['manifest.json + README.md', 'File checksums and guidance'],
          ].map(([name, description]) => (
            <div className="inventory-row" key={name}>
              <Check size={14} />
              <div>
                <code>{name}</code>
                <span>{description}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
      <section aria-label="Individual exports">
        <div className="export-section-heading">
          <h2>Or just what you need</h2>
          <p>Every export reflects the current workspace.</p>
        </div>
        <div className="export-cards">
          {exports.map((item) => (
            <article className="panel export-card" key={item.kind}>
              <div className="export-card-icon">
                <item.icon size={23} strokeWidth={1.5} />
                <span>{item.extension}</span>
              </div>
              <h3>{item.title}</h3>
              <p>{item.description}</p>
              <Button
                variant="secondary"
                onClick={() => void download(item.kind)}
                disabled={busy || item.disabled}
              >
                <ArrowDownToLine size={15} /> Download {item.extension}
              </Button>
              {item.disabled && <small>Add text in the manuscript editor first.</small>}
            </article>
          ))}
        </div>
      </section>
      <section className="panel restore-panel">
        <div className="restore-icon">
          <FolderInput size={25} strokeWidth={1.5} />
        </div>
        <div>
          <h3>Continue a saved project</h3>
          <p>
            Open a ManuscriptForge project JSON. Source hashes and passage links are checked before
            it replaces this workspace.
          </p>
        </div>
        <Button variant="secondary" onClick={restoreProject} disabled={busy}>
          Restore project <ArrowUpRight size={15} />
        </Button>
      </section>
      {analysis.pendingCount > 0 && (
        <div className="export-pending">
          <span className="status-dot pending" />
          <p>
            <strong>{analysis.pendingCount} passages await review.</strong> Your export includes
            them, but descriptive statistics use only approved passages.
          </p>
          <button onClick={() => navigate('review')}>
            Review now <ArrowUpRight size={14} />
          </button>
        </div>
      )}
      <div className="files-bottom-note">
        <ShieldCheck size={15} />
        <span>
          Exports can contain source prose and private notes. Original PDF and Word files are not
          included; their extracted text is.
        </span>
      </div>
    </div>
  )
}
