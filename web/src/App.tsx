import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  ClipboardList,
  Download,
  FileArchive,
  FileText,
  FolderOpen,
  Code2,
  Layers3,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  Menu,
  PenLine,
  Plus,
  Search,
  Sparkles,
  Upload,
  X,
} from 'lucide-react'
import {
  analyzeWorkspace,
  createDemoWorkspace,
  createDocument,
  createEmptyWorkspace,
} from './lib/analysis'
import type { Workspace } from './lib/types'
import { loadWorkspace, saveWorkspace, STORAGE_KEY } from './lib/storage'
import {
  downloadBlob,
  exportManuscriptDocx,
  exportWorkspaceZip,
  parseWorkspace,
  readDocumentFile,
  safeFilename,
  verifyWorkspaceHashes,
} from './lib/files'
import { Button, Mark, type Page, type PageProps, type ExportKind } from './ui'
import Overview from './pages/Overview'
import Library from './pages/Library'
import Review from './pages/Review'
import Style from './pages/Style'
import Desk from './pages/Desk'
import Exports from './pages/Exports'

const NAV = [
  {
    id: 'overview',
    label: 'Overview',
    icon: LayoutDashboard,
    subtitle: 'A little perspective on your work in progress.',
  },
  {
    id: 'corpus',
    label: 'Source library',
    icon: FolderOpen,
    subtitle: 'The original writing behind every observation.',
  },
  {
    id: 'review',
    label: 'Passage review',
    icon: ClipboardList,
    subtitle: 'Give every passage a place. Or leave it out.',
  },
  {
    id: 'style',
    label: 'Style atlas',
    icon: BarChart3,
    subtitle: 'A descriptive portrait of the writing you’ve selected.',
  },
  {
    id: 'manuscript',
    label: 'Writing desk',
    icon: PenLine,
    subtitle: 'Room to think, with your sources close by.',
  },
  {
    id: 'exports',
    label: 'Export & provenance',
    icon: FileArchive,
    subtitle: 'Take your writing, decisions, and source trail with you.',
  },
] as const

export default function App() {
  const [workspace, setWorkspace] = useState<Workspace | null>(null)
  const [page, setPage] = useState<Page>('overview')
  const [toast, setToast] = useState('')
  const [storageError, setStorageError] = useState('')
  const [recoveryData, setRecoveryData] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [mobile, setMobile] = useState(false)
  const [modal, setModal] = useState<'new' | 'search' | 'about' | null>(null)
  const [replacement, setReplacement] = useState<Workspace | null>(null)
  const [search, setSearch] = useState('')
  const [sourceToOpen, setSourceToOpen] = useState<string>()
  const input = useRef<HTMLInputElement>(null)
  const restoreInput = useRef<HTMLInputElement>(null)
  const modalRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    let active = true
    void (async () => {
      try {
        const initial = loadWorkspace() ?? (await createDemoWorkspace())
        await verifyWorkspaceHashes(initial)
        if (active) setWorkspace(initial)
      } catch {
        if (active) {
          try {
            setRecoveryData(localStorage.getItem(STORAGE_KEY))
          } catch {
            /* Storage itself may be disabled. */
          }
          setStorageError(
            'Saved data could not be loaded. Your stored project is preserved; this demo is temporary. Recover the saved data before replacing it.',
          )
          setWorkspace(await createDemoWorkspace())
        }
      }
    })()
    return () => {
      active = false
    }
  }, [])
  useEffect(() => {
    if (!workspace || storageError) return
    try {
      saveWorkspace(workspace)
    } catch {
      setStorageError(
        'Browser storage is unavailable or full. Export a backup before closing this tab.',
      )
    }
  }, [workspace, storageError])
  useEffect(() => {
    if (!toast) return
    const id = window.setTimeout(() => setToast(''), 6500)
    return () => clearTimeout(id)
  }, [toast])
  useEffect(() => {
    const keys = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
        event.preventDefault()
        setModal((v) => (v === 'search' ? null : 'search'))
      }
      if (event.key === 'Escape') {
        setModal(null)
        setReplacement(null)
        setMobile(false)
      }
    }
    window.addEventListener('keydown', keys)
    return () => window.removeEventListener('keydown', keys)
  }, [])
  useEffect(() => {
    if (!modal && !replacement) return
    const previous = document.activeElement as HTMLElement | null
    const panel = modalRef.current
    panel?.querySelector<HTMLElement>('input, button, textarea')?.focus()
    function trap(event: KeyboardEvent) {
      if (event.key !== 'Tab' || !panel) return
      const nodes = Array.from(
        panel.querySelectorAll<HTMLElement>('button:not(:disabled), input, textarea, a[href]'),
      )
      if (event.shiftKey && document.activeElement === nodes[0]) {
        event.preventDefault()
        nodes.at(-1)?.focus()
      }
      if (!event.shiftKey && document.activeElement === nodes.at(-1)) {
        event.preventDefault()
        nodes[0]?.focus()
      }
    }
    document.addEventListener('keydown', trap)
    return () => {
      document.removeEventListener('keydown', trap)
      previous?.focus()
    }
  }, [modal, replacement])
  const analysis = useMemo(() => (workspace ? analyzeWorkspace(workspace) : null), [workspace])
  function navigate(next: Page) {
    setPage(next)
    setModal(null)
    setMobile(false)
    window.scrollTo(0, 0)
  }
  function update(fn: (current: Workspace) => Workspace) {
    setWorkspace((current) =>
      current ? { ...fn(current), updatedAt: new Date().toISOString() } : current,
    )
  }
  async function importFiles(files: FileList | null) {
    if (!files?.length || busy) return
    setBusy(true)
    let working = workspace!
    let added = 0
    const failures: string[] = []
    for (const file of Array.from(files)) {
      try {
        const read = await readDocumentFile(file)
        const result = await createDocument(read.name, read.text, read.format)
        const candidate = {
          ...working,
          documents: [...working.documents, result.document],
          passages: [...working.passages, ...result.passages],
        }
        parseWorkspace(JSON.stringify(candidate))
        working = candidate
        update(() => candidate)
        added++
      } catch (error) {
        failures.push(`${file.name}: ${error instanceof Error ? error.message : String(error)}`)
      }
    }
    setBusy(false)
    setToast(
      `${added ? `${added} source(s) imported. Passages are ready for review.` : 'No sources imported.'} ${failures.join(' ')}`,
    )
    if (added) navigate('corpus')
    if (input.current) input.current.value = ''
  }
  async function restore(file?: File) {
    if (!file) return
    try {
      if (file.size > 20 * 1024 * 1024) throw new Error('Project files must be smaller than 20 MB.')
      const restored = parseWorkspace(await file.text())
      await verifyWorkspaceHashes(restored)
      setReplacement(restored)
    } catch (error) {
      setToast(
        `Could not restore project: ${error instanceof Error ? error.message : String(error)}`,
      )
    }
    if (restoreInput.current) restoreInput.current.value = ''
  }
  async function download(kind: ExportKind) {
    if (!workspace) return
    setBusy(true)
    try {
      const name = safeFilename(workspace.name)
      if (kind === 'zip') downloadBlob(await exportWorkspaceZip(workspace), `${name}-workspace.zip`)
      if (kind === 'docx')
        downloadBlob(await exportManuscriptDocx(workspace), `${name}-manuscript.docx`)
      if (kind === 'md')
        downloadBlob(
          new Blob([workspace.manuscript], { type: 'text/markdown;charset=utf-8' }),
          `${name}-manuscript.md`,
        )
      if (kind === 'json')
        downloadBlob(
          new Blob([JSON.stringify(workspace, null, 2)], { type: 'application/json' }),
          `${name}-project.json`,
        )
      setToast('Export ready. Check your browser’s downloads.')
    } catch (error) {
      setToast(`Export failed: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setBusy(false)
    }
  }
  if (!workspace || !analysis)
    return (
      <main className="startup">
        <Mark />
        <LoaderCircle className="spin" />
        <p>Opening your writing workspace…</p>
      </main>
    )
  const props: PageProps = {
    workspace,
    analysis,
    update,
    navigate,
    notify: setToast,
    importDocuments: () => input.current?.click(),
    restoreProject: () => restoreInput.current?.click(),
    download,
    busy,
    sourceToOpen,
  }
  const currentNav = NAV.find((item) => item.id === page)!
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <input
        ref={input}
        className="sr-only"
        aria-label="Import source documents"
        type="file"
        multiple
        accept=".md,.txt,.docx,.pdf"
        onChange={(event) => void importFiles(event.target.files)}
      />
      <input
        ref={restoreInput}
        className="sr-only"
        aria-label="Restore project JSON"
        type="file"
        accept=".json"
        onChange={(event) => void restore(event.target.files?.[0])}
      />
      {mobile && (
        <button
          className="nav-backdrop"
          aria-label="Close navigation"
          onClick={() => setMobile(false)}
        />
      )}
      <aside className={`sidebar ${mobile ? 'mobile-open' : ''}`}>
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault()
            navigate('overview')
          }}
        >
          <Mark />
          <span>
            Manuscript<span className="brand-light">Forge</span>
            <small>A WORKSPACE FOR YOUR WORDS</small>
          </span>
        </a>
        <button className="project-picker" onClick={() => setModal('new')}>
          <span className="project-avatar">{workspace.name.slice(0, 1).toUpperCase()}</span>
          <span>
            <small>YOUR WORKSPACE</small>
            <strong>{workspace.name}</strong>
          </span>
          <ChevronDown size={15} />
        </button>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {NAV.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={`nav-item ${page === id ? 'active' : ''}`}
              aria-current={page === id ? 'page' : undefined}
              onClick={() => navigate(id)}
            >
              <Icon size={18} strokeWidth={1.7} />
              <span>{label}</span>
              {id === 'review' && analysis.pendingCount > 0 && (
                <span className="nav-count">{analysis.pendingCount}</span>
              )}
              {id === 'overview' && <span className="active-dot" />}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-card">
            <LockKeyhole size={17} />
            <strong>Your work stays yours.</strong>
            <p>Documents are processed in your browser. No account. No uploads.</p>
            <button onClick={() => setModal('about')}>
              How it works <ArrowUpRight size={13} />
            </button>
          </div>
          <div className="sidebar-links">
            <a
              href="https://github.com/ericrosenn1/manuscriptforge"
              target="_blank"
              rel="noreferrer"
            >
              <Code2 size={16} />
              GitHub
              <ArrowUpRight size={12} />
            </a>
            <button onClick={() => setModal('about')} aria-label="About ManuscriptForge">
              <CircleHelp size={18} />
            </button>
          </div>
          <div className="sidebar-version">
            <span className="status-dot" />
            LOCAL-FIRST STUDIO<span>v0.1</span>
          </div>
        </div>
      </aside>
      <div className="workspace-shell">
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              className="mobile-menu icon-button"
              onClick={() => setMobile(true)}
              aria-label="Open navigation"
            >
              <Menu size={20} />
            </button>
            <Layers3 size={16} />
            <span>Workspace</span>
            <ChevronRight size={13} />
            <strong>{currentNav.label}</strong>
          </div>
          <div className="topbar-right">
            <button className="search-trigger" onClick={() => setModal('search')}>
              <Search size={15} />
              <span>Find anything…</span>
              <kbd>⌘ K</kbd>
            </button>
            <span className={`save-status ${storageError ? 'warning' : ''}`}>
              <span className="status-dot" />
              {storageError ? 'Backup needed' : 'Saved locally'}
            </span>
            <button
              className="user-avatar"
              onClick={() => setModal('about')}
              aria-label="About your local workspace"
            >
              MF
            </button>
          </div>
        </header>
        <main id="main" className="main-content">
          {storageError && (
            <div className="storage-warning" role="alert">
              {storageError}
              <button
                onClick={() =>
                  recoveryData !== null
                    ? downloadBlob(
                        new Blob([recoveryData], { type: 'application/json' }),
                        'manuscriptforge-recovery.json',
                      )
                    : void download('json')
                }
              >
                {recoveryData !== null ? 'Recover saved data' : 'Download current backup'}
              </button>
            </div>
          )}
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                MANUSCRIPTFORGE STUDIO <span>/</span>{' '}
                {page === 'overview' ? 'THE BIG PICTURE' : currentNav.label.toUpperCase()}
              </div>
              <h1>{currentNav.label}</h1>
              <p>{currentNav.subtitle}</p>
            </div>
            <div className="heading-actions">
              <Button variant="secondary" onClick={() => void download('zip')} disabled={busy}>
                <Download size={16} />
                Export project
              </Button>
              <Button onClick={() => input.current?.click()} disabled={busy}>
                {busy ? <LoaderCircle size={16} className="spin" /> : <Plus size={17} />}Add
                documents
              </Button>
            </div>
          </div>
          {page === 'overview' && <Overview {...props} newProject={() => setModal('new')} />}
          {page === 'corpus' && <Library {...props} />}
          {page === 'review' && <Review {...props} />}
          {page === 'style' && <Style {...props} />}
          {page === 'manuscript' && <Desk {...props} />}
          {page === 'exports' && <Exports {...props} />}
          <footer className="main-footer">
            <span>Made for thoughtful work.</span>
            <span>
              Created by Eric H. Rosenn <span className="footer-dot">·</span>
              <a
                href="https://github.com/ericrosenn1/manuscriptforge"
                target="_blank"
                rel="noreferrer"
              >
                Explore the source
                <ArrowUpRight size={12} />
              </a>
            </span>
          </footer>
        </main>
      </div>
      {toast && (
        <div className="toast" role="status">
          <Check size={18} />
          <span>{toast}</span>
          <button aria-label="Dismiss notification" onClick={() => setToast('')}>
            <X size={15} />
          </button>
        </div>
      )}
      {(modal || replacement) && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget) {
              setModal(null)
              setReplacement(null)
            }
          }}
        >
          <div
            className={`modal ${modal === 'search' ? 'search-modal' : ''}`}
            ref={modalRef}
            role="dialog"
            aria-modal="true"
            aria-label={
              replacement
                ? 'Replace workspace'
                : modal === 'new'
                  ? 'Start a workspace'
                  : modal === 'search'
                    ? 'Search workspace'
                    : 'About ManuscriptForge'
            }
          >
            <button
              className="modal-close icon-button"
              aria-label="Close dialog"
              onClick={() => {
                setModal(null)
                setReplacement(null)
              }}
            >
              <X size={19} />
            </button>
            {replacement ? (
              <>
                <h2>Open “{replacement.name}”?</h2>
                <p>
                  This replaces the active workspace in this browser. Export the current project
                  first if you want to keep it.
                </p>
                <div className="modal-actions">
                  <Button variant="secondary" onClick={() => void download('json')}>
                    Back up current project
                  </Button>
                  <Button
                    onClick={() => {
                      setWorkspace(replacement)
                      setReplacement(null)
                      setModal(null)
                      setStorageError('')
                      navigate('overview')
                      setToast('Workspace opened.')
                    }}
                  >
                    Open workspace
                    <ArrowRight size={15} />
                  </Button>
                </div>
              </>
            ) : modal === 'new' ? (
              <>
                <h2>A fresh page.</h2>
                <p>
                  Create a workspace for your writing collection. You can back up your current
                  project before switching.
                </p>
                <form
                  onSubmit={(e) => {
                    e.preventDefault()
                    const data = new FormData(e.currentTarget)
                    const name = String(data.get('name')).trim()
                    if (name) {
                      const next = createEmptyWorkspace(name)
                      next.description = String(data.get('description') ?? '').trim()
                      setReplacement(next)
                    }
                  }}
                >
                  <label className="field-label">
                    Workspace name
                    <input
                      name="name"
                      maxLength={100}
                      required
                      placeholder="e.g. My methods paper"
                    />
                  </label>
                  <label className="field-label">
                    A little context (optional)
                    <textarea
                      name="description"
                      maxLength={500}
                      placeholder="What are you working toward?"
                    />
                  </label>
                  <Button>
                    Continue
                    <ArrowRight size={15} />
                  </Button>
                </form>
                <div className="modal-divider">
                  <span>OR EXPLORE FIRST</span>
                </div>
                <div className="modal-actions">
                  <Button
                    variant="secondary"
                    onClick={() => {
                      void createDemoWorkspace().then(setReplacement)
                    }}
                  >
                    <Sparkles size={15} />
                    Load synthetic demo
                  </Button>
                  <Button variant="ghost" onClick={() => restoreInput.current?.click()}>
                    <Upload size={15} />
                    Restore project
                  </Button>
                </div>
              </>
            ) : modal === 'search' ? (
              <>
                <label className="search-modal-input">
                  <Search size={21} />
                  <input
                    autoFocus
                    placeholder="Find a page, source, or passage…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    aria-label="Search workspace"
                  />
                </label>
                <div className="search-results">
                  <div className="eyebrow">PAGES</div>
                  {NAV.filter((item) =>
                    `${item.label} ${item.subtitle}`.toLowerCase().includes(search.toLowerCase()),
                  ).map(({ id, label, icon: Icon }) => (
                    <button key={id} onClick={() => navigate(id)}>
                      <Icon size={17} />
                      <span>{label}</span>
                      <ArrowRight size={14} />
                    </button>
                  ))}
                  {search.trim() && (
                    <>
                      <div className="eyebrow">SOURCES</div>
                      {workspace.documents
                        .filter((d) =>
                          `${d.name} ${d.text}`.toLowerCase().includes(search.toLowerCase()),
                        )
                        .slice(0, 5)
                        .map((d) => (
                          <button
                            key={d.id}
                            onClick={() => {
                              setSourceToOpen(d.id)
                              navigate('corpus')
                            }}
                          >
                            <FileText size={17} />
                            <span>{d.name}</span>
                            <ArrowRight size={14} />
                          </button>
                        ))}
                    </>
                  )}
                </div>
                <div className="search-hint">
                  Local search only
                  <span>
                    <kbd>esc</kbd> to close
                  </span>
                </div>
              </>
            ) : (
              <>
                <Mark />
                <h2>
                  Thoughtful tools.
                  <br />
                  Traceable writing.
                </h2>
                <p>
                  Collect writing samples, curate passages, explore descriptive style, and draft
                  your manuscript.
                </p>
                <div className="about-facts">
                  <p>
                    <LockKeyhole size={18} />
                    <span>
                      <strong>Local to your browser</strong>Documents aren’t sent to a server.
                      Browser storage can be cleared; export backups for important work.
                    </span>
                  </p>
                  <p>
                    <Layers3 size={18} />
                    <span>
                      <strong>Original text, visible decisions</strong>Fingerprints identify
                      normalized extracted text. No hidden generation or invented citations.
                    </span>
                  </p>
                  <p>
                    <BookOpen size={18} />
                    <span>
                      <strong>Descriptive, not evaluative</strong>Heuristic metrics aren’t scores,
                      scientific validation, or proof of personal writing style.
                    </span>
                  </p>
                </div>
                <a
                  className="button secondary"
                  href="https://github.com/ericrosenn1/manuscriptforge"
                  target="_blank"
                  rel="noreferrer"
                >
                  <Code2 size={16} />
                  Code & documentation
                  <ArrowUpRight size={14} />
                </a>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
