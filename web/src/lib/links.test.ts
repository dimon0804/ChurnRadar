import { describe, expect, it } from 'vitest'
import { grafanaUrl, SWAGGER_PATH } from './links'

describe('grafanaUrl', () => {
  it('uses the configured address first', () => {
    expect(grafanaUrl(' https://grafana.example.test ', { protocol: 'http:', hostname: 'localhost' })).toBe(
      'https://grafana.example.test',
    )
  })

  it('falls back to the panel host on port 3000', () => {
    expect(grafanaUrl(undefined, { protocol: 'http:', hostname: 'localhost' })).toBe('http://localhost:3000')
    expect(grafanaUrl('', { protocol: 'https:', hostname: '10.0.0.5' })).toBe('https://10.0.0.5:3000')
    expect(grafanaUrl(undefined, { protocol: 'http:', hostname: '::1' })).toBe('http://[::1]:3000')
  })

  it('points to the Swagger UI of the API', () => {
    expect(SWAGGER_PATH).toBe('/api/v1/docs')
  })
})
