import { ApiError, request } from '@/api/client'
import { api } from '@/api/endpoints'
import type { SnapshotAccepted, SourceSnapshot } from '@/api/types'
import { CASE_DATA_URL, CASE_REFERENCE_TIME, type DemoApi } from './steps'

async function loadCaseData(): Promise<SourceSnapshot> {
  let response: Response
  try {
    response = await fetch(CASE_DATA_URL, { credentials: 'same-origin', cache: 'no-cache' })
  } catch {
    throw new ApiError(0, 'network_error', 'Не удалось загрузить данные кейса: нет связи с сервером.')
  }
  if (!response.ok) throw new ApiError(response.status, 'case_data_missing', 'Файл с данными кейса не найден в сборке панели.')
  return (await response.json()) as SourceSnapshot
}

export const demoApi: DemoApi = {
  topHighRisk: async () => {
    const page = await api.customers({ level: 'high', sort: 'risk_score', order: 'desc', page: 1, page_size: 1 })
    const first = page.items[0]
    if (!first) return null
    const detail = await api.customer(first.id)
    const action = detail.next_action ?? detail.recommendations[0] ?? null
    return { id: first.id, email: first.email, nextAction: action?.title ?? null }
  },
  settings: () => api.settings(),
  patchWeight: (code, weight) => api.patchSettings({ weights: [{ code, weight }] }),
  resetSettings: () => api.resetSettings(),
  simulator: (command) => api.putSimulator(command),
  restoreCaseData: async () => {
    const data = await loadCaseData()
    const accepted = await request<SnapshotAccepted>('/api/v1/source/snapshot', {
      method: 'PUT',
      query: { reset_history: true },
      body: { ...data, reference_time: CASE_REFERENCE_TIME },
    })
    return { accounts: accepted.counts.accounts }
  },
  logout: () => api.logout(),
}
