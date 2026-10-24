import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Mails, Send } from 'lucide-react'
import { useEffect, useState } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { Campaign, CampaignRequest } from '@/api/types'
import { LetterPreview } from '@/components/email-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent } from '@/components/ui/dialog'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { campaignSummary, TEMPLATE_TITLE } from '@/lib/contact-today'
import { countLabel } from '@/lib/format'

export type CampaignTarget = { worklist: true } | { customer_ids: number[] } | { reason: string; reason_scope: 'main' | 'any' }

const AUTO = 'auto'
const SKIP_OPTIONS = [
  { value: '7', label: 'не писать тем, кому писали за 7 дней' },
  { value: '3', label: 'не писать тем, кому писали за 3 дня' },
  { value: '14', label: 'не писать тем, кому писали за 14 дней' },
  { value: '30', label: 'не писать тем, кому писали за 30 дней' },
  { value: '0', label: 'писать всем, даже недавним' },
]

/** Итог рассылки одной строкой: «Отправлено 12, ошибок 1, пропущено 3». */
export function campaignResult(c: Campaign): string {
  const parts = [`Отправлено ${c.sent}`, `ошибок ${c.failed}`]
  if (c.skipped > 0) parts.push(`пропущено ${c.skipped}`)
  return parts.join(', ')
}

function Progress({ campaign }: { campaign: Campaign }) {
  const toSend = campaign.total - campaign.skipped
  const done = campaign.sent + campaign.failed
  const share = toSend > 0 ? done / toSend : 1
  const finished = campaign.status === 'done'
  return (
    <div className="space-y-3" data-testid="campaign-progress" aria-live="polite">
      <div className="h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${Math.round(share * 100)}%` }} />
      </div>
      {finished ? (
        <p className="flex items-center gap-2 text-sm font-medium" data-testid="campaign-result">
          <CheckCircle2 className="size-4 text-risk-low" aria-hidden />
          Готово. {campaignResult(campaign)}.
        </p>
      ) : (
        <p className="text-sm text-muted-foreground num">
          Отправляем: {done} из {toSend}. Письма уходят в фоне — окно можно закрыть.
        </p>
      )}
    </div>
  )
}

/** «Написать всем в очереди» или «Написать выбранным»: предпросмотр, отправка в фоне, прогресс. */
export function CampaignDialog({
  target,
  title,
  open,
  onOpenChange,
  onStarted,
}: {
  target: CampaignTarget
  title: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onStarted?: () => void
}) {
  const queryClient = useQueryClient()
  // письмо всем с причиной: по умолчанию шаблон этой причины, если он есть (иначе — по главной причине)
  const reasonTemplate = 'reason' in target && target.reason in TEMPLATE_TITLE ? target.reason : AUTO
  const [template, setTemplate] = useState(reasonTemplate)
  const [skipDays, setSkipDays] = useState('7')
  const [campaignId, setCampaignId] = useState<number | null>(null)
  const templates = useQuery({ queryKey: queryKeys.mailTemplates, queryFn: ({ signal }) => api.mailTemplates(signal) })

  const request: CampaignRequest = {
    ...('worklist' in target
      ? { worklist: true }
      : 'reason' in target
        ? { worklist: false, reason: target.reason }
        : { worklist: false, customer_ids: target.customer_ids }),
    reason_scope: 'reason' in target ? target.reason_scope : 'any',
    template_code: template === AUTO ? null : template,
    skip_days: Number(skipDays),
  }
  const preview = useQuery({
    queryKey: ['mail-campaign-preview', request],
    queryFn: ({ signal }) => api.previewCampaign(request, signal),
    enabled: open && campaignId === null,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
  const create = useMutation({
    mutationFn: () => api.createCampaign(request),
    onSuccess: (c) => {
      setCampaignId(c.id)
      queryClient.setQueryData(queryKeys.campaign(c.id), c)
      onStarted?.()
    },
  })
  const campaign = useQuery({
    queryKey: queryKeys.campaign(campaignId ?? 0),
    queryFn: ({ signal }) => api.campaign(campaignId ?? 0, signal),
    enabled: campaignId !== null,
    refetchInterval: (q) => (q.state.data?.status === 'done' ? false : 1000),
  })
  const finished = campaign.data?.status === 'done'
  useEffect(() => {
    if (finished) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.customers })
      void queryClient.invalidateQueries({ queryKey: queryKeys.customerAll })
      void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns })
    }
  }, [finished, queryClient])

  const templateOptions = [
    { value: AUTO, label: 'По главной причине каждого клиента' },
    ...(templates.data?.items.map((t) => ({ value: t.code, label: t.title })) ??
      Object.entries(TEMPLATE_TITLE).map(([value, label]) => ({ value, label }))),
  ]
  const p = preview.data

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setCampaignId(null)
          create.reset()
        }
        onOpenChange(next)
      }}
    >
      <DialogContent
        title={
          <span className="flex items-center gap-2">
            <Mails className="size-4 text-muted-foreground" aria-hidden />
            {title}
          </span>
        }
        description="Письма уходят в фоне и не быстрее нескольких в секунду. Каждый получатель получает отметку «Письмо отправлено»."
        data-testid="campaign-dialog"
        footer={
          campaignId === null ? (
            <>
              <DialogClose asChild>
                <Button variant="outline">Отмена</Button>
              </DialogClose>
              <Button
                onClick={() => create.mutate()}
                disabled={!p || p.to_send === 0 || !p.enabled || create.isPending}
                data-testid="campaign-send"
              >
                <Send />
                {p ? `Отправить ${countLabel(p.to_send, ['письмо', 'письма', 'писем'])}` : 'Отправить'}
              </Button>
            </>
          ) : (
            <DialogClose asChild>
              <Button variant={finished ? 'default' : 'outline'}>{finished ? 'Готово' : 'Закрыть, пусть отправляется'}</Button>
            </DialogClose>
          )
        }
      >
        {campaignId !== null ? (
          campaign.data ? (
            <Progress campaign={campaign.data} />
          ) : (
            <Skeleton className="h-10 w-full" />
          )
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1.5 text-[13px]">
                <span className="text-muted-foreground">Шаблон</span>
                <Select value={template} onValueChange={setTemplate} options={templateOptions} ariaLabel="Шаблон письма" className="w-full" />
              </label>
              <label className="space-y-1.5 text-[13px]">
                <span className="text-muted-foreground">Повторы</span>
                <Select value={skipDays} onValueChange={setSkipDays} options={SKIP_OPTIONS} ariaLabel="Защита от повторной отправки" className="w-full" />
              </label>
            </div>
            {preview.isPending ? (
              <Skeleton className="h-40 w-full" />
            ) : preview.isError ? (
              <p role="alert" className="text-sm text-risk-high">
                {errorMessage(preview.error)}
              </p>
            ) : p ? (
              <>
                <p className="text-sm font-medium" data-testid="campaign-summary">
                  {campaignSummary(p, Number(skipDays))}.
                </p>
                {!p.enabled && (
                  <p role="alert" className="rounded-md bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
                    Отправка писем не настроена на сервере (нет SMTP_HOST).
                  </p>
                )}
                {p.templates.length > 1 && (
                  <p className="text-[13px] text-muted-foreground">
                    Шаблоны:{' '}
                    {p.templates
                      .map((t) => `${templates.data?.items.find((x) => x.code === t.code)?.title ?? TEMPLATE_TITLE[t.code] ?? t.code} — ${t.count}`)
                      .join(', ')}
                  </p>
                )}
                {p.sample && (
                  <div className="space-y-1.5">
                    <p className="text-[13px] text-muted-foreground">Пример: первое письмо рассылки</p>
                    <LetterPreview to={p.sample.to} subject={p.sample.subject} text={p.sample.text} />
                  </div>
                )}
              </>
            ) : null}
            {create.isError && (
              <p role="alert" className="text-[13px] text-risk-high">
                {errorMessage(create.error)}
              </p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
