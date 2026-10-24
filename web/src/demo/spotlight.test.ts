// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { findTarget, findTargets, waitForTarget } from './spotlight'
import { sheetSection, sortHeader, weightRow } from './steps'

afterEach(() => {
  document.body.innerHTML = ''
})

describe('findTarget', () => {
  it('finds a section of the customer sheet by its heading', () => {
    document.body.innerHTML = `
      <div role="dialog">
        <section id="why"><h3>Почему</h3></section>
        <section id="todo"><h3>Что сделать</h3></section>
      </div>`
    expect(findTarget(sheetSection('Почему'))?.id).toBe('why')
    expect(findTarget(sheetSection('Что сделать'))?.id).toBe('todo')
  })

  it('prefers an exact text match and falls back to a prefix', () => {
    document.body.innerHTML = `
      <table><thead><tr>
        <th id="a">Баланс бонусов</th><th id="b">Баланс</th><th id="c">Пополнено всего ↓</th>
      </tr></thead></table>`
    expect(findTarget(sortHeader('Баланс'))?.id).toBe('b')
    expect(findTarget(sortHeader('Пополнено всего'))?.id).toBe('c')
  })

  it('climbs to the model weight row', () => {
    document.body.innerHTML = `<ul><li id="row"><div><p>Ждёт оплаты</p><p>описание</p></div></li></ul>`
    expect(findTarget(weightRow('Ждёт оплаты'))?.id).toBe('row')
  })

  it('returns nothing for missing elements and skips duplicates', () => {
    document.body.innerHTML = `<div data-testid="kpi-high" id="k"></div>`
    expect(findTarget({ selector: '[data-testid="nope"]' })).toBeNull()
    const found = findTargets([{ selector: '[data-testid="kpi-high"]' }, { selector: '#k' }, { selector: '#missing' }])
    expect(found.map((el) => el.id)).toEqual(['k'])
  })
})

describe('waitForTarget', () => {
  it('waits for an element rendered later', async () => {
    window.setTimeout(() => {
      document.body.innerHTML = '<p id="late">готово</p>'
    }, 30)
    const el = await waitForTarget({ selector: '#late' }, 1000, 10)
    expect(el?.id).toBe('late')
  })

  it('gives up after the timeout', async () => {
    expect(await waitForTarget({ selector: '#never' }, 40, 10)).toBeNull()
  })
})
