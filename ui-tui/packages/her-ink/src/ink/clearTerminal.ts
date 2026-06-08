/**
 * Terminal clearing with scrollback support.
 */

import { CURSOR_HOME, ERASE_SCREEN, ERASE_SCROLLBACK } from './termio/csi.js'

/**
 * Returns the ANSI escape sequence to clear the terminal including scrollback.
 */
export function getClearTerminalSequence(): string {
  return ERASE_SCREEN + ERASE_SCROLLBACK + CURSOR_HOME
}

/**
 * Clears the terminal screen. On supported terminals, also clears scrollback.
 */
export const clearTerminal = getClearTerminalSequence()
