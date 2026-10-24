const PREFIX = 'cr-demo-'

export function readStored(key: string): string | null {
  try {
    return localStorage.getItem(PREFIX + key)
  } catch {
    return null
  }
}

export function writeStored(key: string, value: string) {
  try {
    localStorage.setItem(PREFIX + key, value)
  } catch {
    // приватный режим: пульт просто не запомнит выбор
  }
}
