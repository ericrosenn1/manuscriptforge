import type { ReactNode } from 'react'
import { FileText } from 'lucide-react'
import type { Workspace, Analysis } from './lib/types'
export type Page = 'overview' | 'corpus' | 'review' | 'style' | 'manuscript' | 'exports'
export type ExportKind = 'zip' | 'docx' | 'md' | 'json'
export interface PageProps {
  workspace: Workspace
  analysis: Analysis
  update: (fn: (current: Workspace) => Workspace) => void
  navigate: (page: Page) => void
  notify: (message: string) => void
  importDocuments: () => void
  restoreProject: () => void
  download: (kind: ExportKind) => Promise<void>
  busy: boolean
  sourceToOpen?: string
}
export const title = (value: string) => value.charAt(0).toUpperCase() + value.slice(1)
export const number = (value: number) => value.toLocaleString('en-US', { maximumFractionDigits: 1 })
export function Mark() {
  return (
    <span className="brand-mark" aria-hidden="true">
      <svg viewBox="0 0 32 32">
        <path d="M7 24V8l9 10 9-10v16M7 8l9 16 9-16" />
      </svg>
    </span>
  )
}
export function Button({
  children,
  onClick,
  variant = 'primary',
  disabled,
  className = '',
}: {
  children: ReactNode
  onClick?: () => void
  variant?: 'primary' | 'secondary' | 'ghost'
  disabled?: boolean
  className?: string
}) {
  return (
    <button className={`button ${variant} ${className}`} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  )
}
export function Badge({ children, kind = 'neutral' }: { children: ReactNode; kind?: string }) {
  return <span className={`badge ${kind}`}>{children}</span>
}
export function Empty({ heading, children }: { heading: string; children: ReactNode }) {
  return (
    <div className="empty">
      <FileText size={30} strokeWidth={1.4} />
      <h3>{heading}</h3>
      <div>{children}</div>
    </div>
  )
}
