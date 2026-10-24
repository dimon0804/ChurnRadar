import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api, queryKeys } from '@/api/endpoints'
import type { CustomerActionUpdate } from '@/api/types'

export interface ActionVariables {
  id: number
  /** null — снять отметку. */
  update: CustomerActionUpdate | null
}

/** Отметить клиента «Сделано»/«Отложить» или снять отметку; очередь и карточка обновляются сами. */
export function useCustomerAction() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ id, update }: ActionVariables) =>
      update ? api.putCustomerAction(id, update) : api.deleteCustomerAction(id),
    onSuccess: (state, { id }) => {
      queryClient.setQueryData(queryKeys.customerAction(id), state)
      // очередь «С кем связаться» лежит под префиксом customers
      void queryClient.invalidateQueries({ queryKey: queryKeys.customers })
    },
  })
}
