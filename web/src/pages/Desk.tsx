import { useState, type ReactNode } from 'react'
import { BookOpen, Download, Eye, PenLine, ArrowUpRight, FileText } from 'lucide-react'
import { countWords } from '../lib/analysis'
import { type PageProps, title } from '../ui'
import './desk.css'

function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**'))
      return <strong key={index}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`'))
      return <code key={index}>{part.slice(1, -1)}</code>
    if (part.startsWith('*') && part.endsWith('*')) return <em key={index}>{part.slice(1, -1)}</em>
    return part
  })
}

/** Deliberately render a safe Markdown subset as React text, never injected HTML. */
function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  let paragraph: string[] = []
  let items: string[] = []
  let ordered = false
  let code: string[] | null = null
  const flushParagraph = () => {
    if (paragraph.length)
      blocks.push(<p key={`p-${blocks.length}`}>{inline(paragraph.join('\n'))}</p>)
    paragraph = []
  }
  const flushList = () => {
    if (items.length) {
      const content = items.map((item, index) => <li key={index}>{inline(item)}</li>)
      blocks.push(
        ordered ? (
          <ol key={`l-${blocks.length}`}>{content}</ol>
        ) : (
          <ul key={`l-${blocks.length}`}>{content}</ul>
        ),
      )
    }
    items = []
  }
  for (const line of lines) {
    if (/^```/.test(line)) {
      flushParagraph()
      flushList()
      if (code) {
        blocks.push(
          <pre key={`c-${blocks.length}`}>
            <code>{code.join('\n')}</code>
          </pre>,
        )
        code = null
      } else code = []
      continue
    }
    if (code) {
      code.push(line)
      continue
    }
    if (!line.trim()) {
      flushParagraph()
      flushList()
      continue
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      flushParagraph()
      flushList()
      const content = inline(heading[2])
      const key = `h-${blocks.length}`
      if (heading[1].length === 1) blocks.push(<h1 key={key}>{content}</h1>)
      else if (heading[1].length === 2) blocks.push(<h2 key={key}>{content}</h2>)
      else blocks.push(<h3 key={key}>{content}</h3>)
      continue
    }
    if (/^>\s?/.test(line)) {
      flushParagraph()
      flushList()
      blocks.push(
        <blockquote key={`q-${blocks.length}`}>{inline(line.replace(/^>\s?/, ''))}</blockquote>,
      )
      continue
    }
    const list = /^\s*(?:([-*+])|\d+\.)\s+(.+)$/.exec(line)
    if (list) {
      flushParagraph()
      const nextOrdered = !list[1]
      if (items.length && nextOrdered !== ordered) flushList()
      ordered = nextOrdered
      items.push(list[2])
      continue
    }
    flushList()
    if (/^(?:---+|\*\*\*+)\s*$/.test(line)) {
      flushParagraph()
      blocks.push(<hr key={`r-${blocks.length}`} />)
    } else paragraph.push(line)
  }
  flushParagraph()
  flushList()
  if (code)
    blocks.push(
      <pre key={`c-${blocks.length}`}>
        <code>{code.join('\n')}</code>
      </pre>,
    )
  return <>{blocks}</>
}

export default function Desk({ workspace, update, navigate, download, busy }: PageProps) {
  const [preview, setPreview] = useState(false)
  const approved = workspace.passages.filter((passage) => passage.decision === 'approved')
  const sourceNames = new Map(workspace.documents.map((document) => [document.id, document.name]))
  const words = countWords(workspace.manuscript)
  return (
    <div className="desk-page">
      <div className="desk-layout">
        <section className="desk-paper" aria-label="Manuscript editor">
          <div className="desk-toolbar">
            <span>
              <FileText size={15} /> MANUSCRIPT
            </span>
            <div className="desk-mode" aria-label="Editor display mode">
              <button aria-pressed={!preview} onClick={() => setPreview(false)}>
                <PenLine size={14} /> Write
              </button>
              <button aria-pressed={preview} onClick={() => setPreview(true)}>
                <Eye size={14} /> Preview
              </button>
            </div>
            <button
              className="desk-download"
              onClick={() => void download('docx')}
              disabled={busy || !workspace.manuscript.trim()}
              aria-label="Export manuscript DOCX"
            >
              <Download size={14} /> DOCX
            </button>
          </div>
          {preview ? (
            <article className="desk-preview" aria-label="Manuscript preview">
              {workspace.manuscript.trim() ? (
                <Markdown text={workspace.manuscript} />
              ) : (
                <div className="desk-blank">
                  <PenLine size={26} />
                  <h3>Your next idea starts here.</h3>
                  <p>Switch to Write to begin a manuscript.</p>
                </div>
              )}
            </article>
          ) : (
            <textarea
              className="desk-editor"
              aria-label="Manuscript text"
              spellCheck
              value={workspace.manuscript}
              placeholder={
                '# Your manuscript title\n\n## Introduction\n\nBegin with the question that matters.'
              }
              onChange={(event) =>
                update((current) => ({ ...current, manuscript: event.target.value }))
              }
            />
          )}
          <div className="desk-footer">
            <span>
              {words.toLocaleString()} words <span aria-hidden="true">·</span>{' '}
              {Math.max(1, Math.ceil(words / 200))} min read
            </span>
            <span>Markdown supported</span>
          </div>
        </section>

        <aside className="desk-sources" aria-label="Approved source passages">
          <div className="desk-reference-heading">
            <BookOpen size={19} />
            <span>At your side</span>
          </div>
          <p className="desk-reference-description">
            Your approved passages, here for reference as you write.
          </p>
          <div className="desk-reference-count">
            <span>APPROVED PASSAGES</span>
            <strong>{approved.length.toString().padStart(2, '0')}</strong>
          </div>
          {approved.length ? (
            <div className="desk-reference-list">
              {approved.map((passage) => (
                <details key={passage.id} className="desk-reference">
                  <summary>
                    <span>{title(passage.section)}</span>
                    <span className="desk-source-filename">
                      {sourceNames.get(passage.documentId)}
                    </span>
                  </summary>
                  <p>{passage.text}</p>
                  <span className="desk-reference-hash" title={passage.hash}>
                    SHA-256 {passage.hash.slice(0, 12)}…
                  </span>
                </details>
              ))}
            </div>
          ) : (
            <div className="desk-reference-empty">
              <p>Approve passages in your corpus to keep useful references beside your draft.</p>
            </div>
          )}
          <button className="desk-review-link" onClick={() => navigate('review')}>
            Review your passages <ArrowUpRight size={15} />
          </button>
          <div className="desk-note">
            <span>THE AUTHOR IS YOU</span>
            <p>
              This desk holds your writing. Descriptive style patterns and source text support your
              judgment; they do not generate claims or validate your manuscript.
            </p>
          </div>
        </aside>
      </div>
    </div>
  )
}
