import { afterEach, describe, expect, it, vi } from 'vitest'
import { createDemoWorkspace } from './analysis'
import { clearWorkspace, loadWorkspace, saveWorkspace, STORAGE_KEY } from './storage'

afterEach(() => vi.unstubAllGlobals())

function memoryStorage() {
  const entries = new Map<string, string>()
  return {
    getItem: (key: string) => entries.get(key) ?? null,
    setItem: (key: string, value: string) => {
      entries.set(key, value)
    },
    removeItem: (key: string) => {
      entries.delete(key)
    },
  }
}

describe('local workspace persistence', () => {
  it('restores saved work and clears only this application key', async () => {
    const storage = memoryStorage()
    storage.setItem('unrelated', 'preserve')
    vi.stubGlobal('localStorage', storage)
    expect(loadWorkspace()).toBeNull()
    const workspace = await createDemoWorkspace()
    saveWorkspace(workspace)
    expect(loadWorkspace()).toEqual(workspace)
    clearWorkspace()
    expect(loadWorkspace()).toBeNull()
    expect(storage.getItem('unrelated')).toBe('preserve')
  })

  it('surfaces corruption and quota errors instead of silently losing work', async () => {
    const storage = memoryStorage()
    storage.setItem(STORAGE_KEY, '{broken')
    vi.stubGlobal('localStorage', storage)
    expect(() => loadWorkspace()).toThrow('could not be opened')
    vi.stubGlobal('localStorage', {
      setItem() {
        throw new Error('QuotaExceededError')
      },
    })
    expect(() => saveWorkspace({} as never)).toThrow('Export your project')
  })
})
