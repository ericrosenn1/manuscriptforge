import type { Workspace } from './types'
import { parseWorkspace } from './files'

export const STORAGE_KEY = 'manuscriptforge.workspace.v1'

export function loadWorkspace(): Workspace | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY)
    return value === null ? null : parseWorkspace(value)
  } catch (error) {
    throw new Error(
      `The saved workspace could not be opened. Export or clear the saved project before starting again. ${error instanceof Error ? error.message : ''}`,
    )
  }
}

export function saveWorkspace(workspace: Workspace): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(workspace))
  } catch {
    throw new Error(
      'This browser could not save your workspace. Storage may be full or disabled. Export your project to keep your changes.',
    )
  }
}

export function clearWorkspace(): void {
  try {
    localStorage.removeItem(STORAGE_KEY)
  } catch {
    throw new Error(
      'This browser did not allow saved workspace data to be cleared. Check your browser storage settings.',
    )
  }
}
