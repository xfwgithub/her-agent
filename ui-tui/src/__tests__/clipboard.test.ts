import { describe, expect, it, vi } from 'vitest'

import { isUsableClipboardText, readClipboardText, writeClipboardText } from '../lib/clipboard.js'

describe('readClipboardText', () => {
  it('reads text from pbpaste on macOS', async () => {
    const run = vi.fn().mockResolvedValue({ stdout: 'hello world\n' })

    await expect(readClipboardText('darwin', run)).resolves.toBe('hello world\n')
    expect(run).toHaveBeenCalledWith(
      'pbpaste',
      [],
      expect.objectContaining({ encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 })
    )
  })

  it('uses wl-paste on Wayland Linux', async () => {
    const run = vi.fn().mockResolvedValue({ stdout: 'from wayland\n' })

    await expect(readClipboardText('linux', run, { WAYLAND_DISPLAY: 'wayland-1' } as NodeJS.ProcessEnv)).resolves.toBe(
      'from wayland\n'
    )
    expect(run).toHaveBeenCalledWith(
      'wl-paste',
      ['--type', 'text'],
      expect.objectContaining({ encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 })
    )
  })

  it('falls back to xclip on Linux when wl-paste fails', async () => {
    const run = vi
      .fn()
      .mockRejectedValueOnce(new Error('wl-paste missing'))
      .mockResolvedValueOnce({ stdout: 'from xclip\n' })

    await expect(readClipboardText('linux', run, { WAYLAND_DISPLAY: 'wayland-1' } as NodeJS.ProcessEnv)).resolves.toBe(
      'from xclip\n'
    )
    expect(run).toHaveBeenNthCalledWith(
      1,
      'wl-paste',
      ['--type', 'text'],
      expect.objectContaining({ encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 })
    )
    expect(run).toHaveBeenNthCalledWith(
      2,
      'xclip',
      ['-selection', 'clipboard', '-out'],
      expect.objectContaining({ encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 })
    )
  })

  it('returns null when every clipboard backend fails', async () => {
    const run = vi.fn().mockRejectedValue(new Error('clipboard failed'))

    await expect(
      readClipboardText('linux', run, { WAYLAND_DISPLAY: 'wayland-1' } as NodeJS.ProcessEnv)
    ).resolves.toBeNull()
  })
})

describe('isUsableClipboardText', () => {
  it('accepts normal text', () => {
    expect(isUsableClipboardText('hello world\n')).toBe(true)
  })

  it('rejects empty or whitespace-only content', () => {
    expect(isUsableClipboardText('')).toBe(false)
    expect(isUsableClipboardText('  \n\t')).toBe(false)
  })

  it('rejects binary-looking clipboard payloads', () => {
    expect(isUsableClipboardText('PNG\u0000\u0001\u0002\u0003IHDR')).toBe(false)
    expect(isUsableClipboardText('TIFF\ufffd\ufffd\ufffdmetadata')).toBe(false)
  })
})
describe('writeClipboardText', () => {
  it('does nothing off macOS when no tools are available', async () => {
    const child = {
      once: vi.fn((event: string, cb: (code?: number) => void) => {
        if (event === 'close') {
          cb(1) // non-zero exit = failure
        }

        return child
      }),
      stdin: { end: vi.fn() }
    }

    const start = vi.fn().mockReturnValue(child)

    // Linux with no WAYLAND_DISPLAY — falls through xclip then xsel, both fail
    await expect(writeClipboardText('hello', 'linux', start, {})).resolves.toBe(false)
  })

  it('writes text to pbcopy on macOS', async () => {
    const stdin = { end: vi.fn() }

    const child = {
      once: vi.fn((event: string, cb: (code?: number) => void) => {
        if (event === 'close') {
          cb(0)
        }

        return child
      }),
      stdin
    }

    const start = vi.fn().mockReturnValue(child)

    await expect(writeClipboardText('hello world', 'darwin', start as any)).resolves.toBe(true)
    expect(start).toHaveBeenCalledWith(
      'pbcopy',
      [],
      expect.objectContaining({ stdio: ['pipe', 'ignore', 'ignore'] })
    )
    expect(stdin.end).toHaveBeenCalledWith('hello world')
  })

  it('returns false when pbcopy fails', async () => {
    const child = {
      once: vi.fn((event: string, cb: () => void) => {
        if (event === 'error') {
          cb()
        }

        return child
      }),
      stdin: { end: vi.fn() }
    }

    const start = vi.fn().mockReturnValue(child)

    await expect(writeClipboardText('hello world', 'darwin', start as any)).resolves.toBe(false)
  })

  it('uses wl-copy on Wayland Linux', async () => {
    const stdin = { end: vi.fn() }

    const child = {
      once: vi.fn((event: string, cb: (code?: number) => void) => {
        if (event === 'close') {
          cb(0)
        }

        return child
      }),
      stdin
    }

    const start = vi.fn().mockReturnValue(child)

    await expect(
      writeClipboardText('wayland text', 'linux', start as any, { WAYLAND_DISPLAY: 'wayland-1' })
    ).resolves.toBe(true)
    expect(start).toHaveBeenCalledWith(
      'wl-copy',
      ['--type', 'text/plain'],
      expect.objectContaining({ stdio: ['pipe', 'ignore', 'ignore'] })
    )
    expect(stdin.end).toHaveBeenCalledWith('wayland text')
  })

  it('falls back to xclip when wl-copy fails on Wayland', async () => {
    let callCount = 0
    const stdin = { end: vi.fn() }

    const child = {
      once: vi.fn((event: string, cb: (code?: number) => void) => {
        if (event === 'close') {
          callCount++
          // wl-copy fails, xclip succeeds
          cb(callCount === 1 ? 1 : 0)
        }

        return child
      }),
      stdin
    }

    const start = vi.fn().mockReturnValue(child)

    await expect(
      writeClipboardText('x11 text', 'linux', start as any, { WAYLAND_DISPLAY: 'wayland-1' })
    ).resolves.toBe(true)
    expect(start).toHaveBeenNthCalledWith(
      1,
      'wl-copy',
      ['--type', 'text/plain'],
      expect.anything()
    )
    expect(start).toHaveBeenNthCalledWith(
      2,
      'xclip',
      ['-selection', 'clipboard', '-in'],
      expect.anything()
    )
  })

  it('falls back to xsel when both wl-copy and xclip fail', async () => {
    let callCount = 0
    const stdin = { end: vi.fn() }

    const child = {
      once: vi.fn((event: string, cb: (code?: number) => void) => {
        if (event === 'close') {
          callCount++
          cb(callCount < 3 ? 1 : 0) // first two fail, third (xsel) succeeds
        }

        return child
      }),
      stdin
    }

    const start = vi.fn().mockReturnValue(child)

    await expect(
      writeClipboardText('xsel text', 'linux', start as any, { WAYLAND_DISPLAY: 'wayland-1' })
    ).resolves.toBe(true)
    expect(start).toHaveBeenNthCalledWith(3, 'xsel', ['--clipboard', '--input'], expect.anything())
  })
})
