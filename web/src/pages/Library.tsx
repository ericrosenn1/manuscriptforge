import { useEffect, useState } from 'react'
import {
  ArrowUpRight,
  Copy,
  FileText,
  Fingerprint,
  Plus,
  Search,
  ShieldCheck,
  X,
} from 'lucide-react'
import { Badge, Button, Empty, number, type PageProps } from '../ui'
import './files-pages.css'

export default function Library({
  workspace,
  importDocuments,
  navigate,
  notify,
  busy,
  sourceToOpen,
}: PageProps) {
  const [query, setQuery] = useState('')
  const [format, setFormat] = useState('all')
  const [selectedId, setSelectedId] = useState<string | null>(sourceToOpen ?? null)
  useEffect(() => {
    if (sourceToOpen) setSelectedId(sourceToOpen)
  }, [sourceToOpen])
  const documents = workspace.documents.filter(
    (document) =>
      document.name.toLowerCase().includes(query.toLowerCase()) &&
      (format === 'all' || document.format === format),
  )
  const selected = workspace.documents.find((document) => document.id === selectedId)
  const selectedPassages = workspace.passages.filter((passage) => passage.documentId === selectedId)
  const copyHash = async () => {
    if (!selected) return
    try {
      await navigator.clipboard.writeText(selected.hash)
      notify('Source fingerprint copied.')
    } catch {
      notify('Clipboard unavailable. You can select and copy the fingerprint below.')
    }
  }

  return (
    <div className="files-page">
      <div className="files-intro panel">
        <div className="files-intro-symbol">
          <ShieldCheck size={24} strokeWidth={1.5} />
        </div>
        <div>
          <strong>Your documents stay in this browser.</strong>
          <p>
            Import Markdown, TXT, DOCX, or text-based PDF files. Extraction runs on your device; new
            passages await your review.
          </p>
        </div>
        <span className="files-format-note">
          10 MB / file
          <br />
          50 pages / PDF
        </span>
      </div>
      <section className="panel source-panel" aria-label="Source documents">
        <div className="panel-heading">
          <div>
            <h2>
              Source library <span className="files-count">{workspace.documents.length}</span>
            </h2>
            <p>{number(workspace.passages.length)} extracted passages across your collection</p>
          </div>
          <div className="files-controls">
            <label className="files-search">
              <Search size={16} />
              <input
                aria-label="Search documents"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Find a document…"
              />
            </label>
            <select
              aria-label="Filter document format"
              value={format}
              onChange={(event) => setFormat(event.target.value)}
            >
              <option value="all">All formats</option>
              {['md', 'txt', 'docx', 'pdf'].map((value) => (
                <option value={value} key={value}>
                  {value.toUpperCase()}
                </option>
              ))}
            </select>
          </div>
        </div>
        {!documents.length ? (
          <Empty
            heading={
              workspace.documents.length ? 'No documents match' : 'Your collection starts here'
            }
          >
            <p>
              {workspace.documents.length
                ? 'Try another name or format.'
                : 'Add a writing sample to extract its passages and begin building a descriptive style profile.'}
            </p>
            {!workspace.documents.length && (
              <Button onClick={importDocuments} disabled={busy}>
                <Plus size={15} /> Import your first document
              </Button>
            )}
          </Empty>
        ) : (
          <div className="files-table-scroll">
            <table className="source-table">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Format</th>
                  <th>Passages</th>
                  <th>Review</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {documents.map((document) => {
                  const passages = workspace.passages.filter(
                    (passage) => passage.documentId === document.id,
                  )
                  const approved = passages.filter(
                    (passage) => passage.decision === 'approved',
                  ).length
                  const pending = passages.filter(
                    (passage) => passage.decision === 'pending',
                  ).length
                  const duplicate = workspace.documents.some(
                    (other) => other.id !== document.id && other.hash === document.hash,
                  )
                  return (
                    <tr
                      key={document.id}
                      className={selectedId === document.id ? 'source-row-selected' : ''}
                    >
                      <td>
                        <button
                          className="source-name"
                          onClick={() =>
                            setSelectedId(selectedId === document.id ? null : document.id)
                          }
                          aria-expanded={selectedId === document.id}
                        >
                          <span className="source-file-icon">
                            <FileText size={19} strokeWidth={1.5} />
                          </span>
                          <span>
                            <strong>{document.name}</strong>
                            <small>
                              {new Date(document.addedAt).toLocaleDateString('en-US', {
                                month: 'short',
                                day: 'numeric',
                                year: 'numeric',
                              })}
                              {duplicate ? ' · Duplicate text' : ''}
                            </small>
                          </span>
                          <ArrowUpRight size={15} />
                        </button>
                      </td>
                      <td>
                        <span className="format-label">{document.format.toUpperCase()}</span>
                      </td>
                      <td>{passages.length}</td>
                      <td>
                        <div className="review-count">
                          <span className={pending ? 'status-dot pending' : 'status-dot'} />
                          {pending ? `${pending} pending` : `${approved} approved`}
                        </div>
                      </td>
                      <td>
                        <Badge kind={document.synthetic ? 'neutral' : 'success'}>
                          {document.synthetic ? 'Synthetic demo' : 'Your document'}
                        </Badge>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
      {selected && (
        <section className="panel source-preview" aria-label={`Source preview: ${selected.name}`}>
          <div className="panel-heading">
            <div>
              <div className="eyebrow">EXTRACTED TEXT</div>
              <h2>{selected.name}</h2>
              <p>
                {selectedPassages.length} passages · original formatting simplified during
                extraction
              </p>
            </div>
            <button
              className="icon-button"
              aria-label="Close source preview"
              onClick={() => setSelectedId(null)}
            >
              <X size={18} />
            </button>
          </div>
          <div className="source-fingerprint">
            <Fingerprint size={18} />
            <div>
              <span>Normalized extracted text · SHA-256</span>
              <code>{selected.hash}</code>
            </div>
            <button
              className="icon-button"
              aria-label="Copy source fingerprint"
              onClick={() => void copyHash()}
            >
              <Copy size={16} />
            </button>
          </div>
          <pre className="source-text">{selected.text}</pre>
          <div className="source-preview-footer">
            <span>Source text is retained independently of your review decisions.</span>
            <Button variant="secondary" onClick={() => navigate('review')}>
              Review passages <ArrowUpRight size={14} />
            </Button>
          </div>
        </section>
      )}
      <div className="files-bottom-note">
        <Fingerprint size={15} />
        <span>
          Fingerprints identify normalized extracted text. Uploaded binary originals are not
          retained.
        </span>
      </div>
    </div>
  )
}
