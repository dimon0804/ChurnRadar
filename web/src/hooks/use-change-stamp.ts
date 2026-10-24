import { useState } from 'react'

/**
 * Счётчик изменений значения (0 — ещё не менялось). Удобно как key для элемента,
 * который должен коротко подсветиться после пересчёта: новый key перезапускает анимацию.
 */
export function useChangeStamp<T>(value: T, equals: (a: T, b: T) => boolean = Object.is): number {
  const [state, setState] = useState<{ value: T; stamp: number }>({ value, stamp: 0 })
  if (!equals(state.value, value)) {
    setState({ value, stamp: state.stamp + 1 })
  }
  return state.stamp
}

/** Предыдущее значение, которое было до последнего изменения (null — ещё не менялось). */
export function usePreviousDistinct<T>(value: T, equals: (a: T, b: T) => boolean = Object.is): T | null {
  const [state, setState] = useState<{ value: T; prev: T | null }>({ value, prev: null })
  if (!equals(state.value, value)) {
    setState({ value, prev: state.value })
  }
  return state.prev
}
