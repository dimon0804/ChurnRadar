// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { hasDemoParam, isDemoHotkey, isEditableTarget } from './hotkey'

function press(target: Element, init: KeyboardEventInit): boolean {
  let hit = false
  const listener = (event: KeyboardEvent) => {
    hit = isDemoHotkey(event)
  }
  window.addEventListener('keydown', listener)
  target.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, ...init }))
  window.removeEventListener('keydown', listener)
  return hit
}

const shiftD = { key: 'D', code: 'KeyD', shiftKey: true }

afterEach(() => {
  document.body.innerHTML = ''
})

describe('isDemoHotkey', () => {
  it('fires on Shift+D outside of fields', () => {
    const div = document.body.appendChild(document.createElement('div'))
    expect(press(div, shiftD)).toBe(true)
    expect(press(document.body, shiftD)).toBe(true)
  })

  it('works with the Russian layout by physical key', () => {
    expect(press(document.body, { key: 'В', code: 'KeyD', shiftKey: true })).toBe(true)
  })

  it('stays silent while typing', () => {
    document.body.innerHTML = `
      <input id="i" />
      <textarea id="t"></textarea>
      <select id="s"><option>1</option></select>
      <div id="c" contenteditable="true"><span id="inner">x</span></div>
      <div role="textbox" id="r"></div>`
    for (const id of ['i', 't', 's', 'inner', 'r']) {
      expect(press(document.getElementById(id)!, shiftD), id).toBe(false)
    }
  })

  it('ignores other combinations', () => {
    expect(press(document.body, { key: 'd', code: 'KeyD' })).toBe(false)
    expect(press(document.body, { ...shiftD, ctrlKey: true })).toBe(false)
    expect(press(document.body, { ...shiftD, metaKey: true })).toBe(false)
    expect(press(document.body, { ...shiftD, altKey: true })).toBe(false)
    expect(press(document.body, { ...shiftD, repeat: true })).toBe(false)
    expect(press(document.body, { key: 'F', code: 'KeyF', shiftKey: true })).toBe(false)
  })
})

describe('isEditableTarget', () => {
  it('handles empty and non-element targets', () => {
    expect(isEditableTarget(null)).toBe(false)
    expect(isEditableTarget(window)).toBe(false)
  })
})

describe('hasDemoParam', () => {
  it('reads ?demo', () => {
    expect(hasDemoParam('?demo=1')).toBe(true)
    expect(hasDemoParam('?sort=email&demo')).toBe(true)
    expect(hasDemoParam('?demo=0')).toBe(false)
    expect(hasDemoParam('?demo=false')).toBe(false)
    expect(hasDemoParam('')).toBe(false)
  })
})
