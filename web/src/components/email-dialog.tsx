import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Mail, Send } from 'lucide-react'
import { useState } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { EmailDraft, EmailSent } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Dialog, DialogClose, DialogContent, Textarea } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { TEMPLATE_TITLE } from '@/lib/contact-today'
import { formatDate } from '@/lib/format'
import { cn } from '@/lib/utils'

function useTemplateOptions() {
  const templates = useQuery({ queryKey: queryKeys.mailTemplates, queryFn: ({ signal }) => api.mailTemplates(signal) })
  const items = templates.data?.items
  return items
    ? items.map((t) => ({ value: t.code, label: t.title }))
    : Object.entries(TEMPLATE_TITLE).map(([value, label]) => ({ value, label }))
}

/** Письмо как его увидит клиент. */
export function LetterPreview({ from, to, subject, text, className }: { from?: string; to: string; subject: string; text: string; className?: string }) {
  return (
    <div className={cn('rounded-lg border border-border bg-background', className)} data-testid="letter-preview">
      <div className="space-y-0.5 border-b border-border px-4 py-2.5 text-[13px]">
        {from && <p className="text-muted-foreground">От: {from}</p>}
        <p className="text-muted-foreground">Кому: {to}</p>
        <p className="font-semibold text-foreground">{subject || 'Без темы'}</p>
      </div>
      <p className="px-4 py-3 text-sm leading-6 whitespace-pre-wrap">{text}</p>
    </div>
  )
}

function EmailForm({
  draft,
  templateOptions,
  onTemplate,
  onSent,
}: {
  draft: EmailDraft
  templateOptions: { value: string; label: string }[]
  onTemplate: (code: string) => void
  onSent: (sent: EmailSent) => void
}) {
  const queryClient = useQueryClient()
  const [subject, setSubject] = useState(draft.subject)
  const [text, setText] = useState(draft.text)
  const [view, setView] = useState<'edit' | 'preview'>('edit')
  const send = useMutation({
    mutationFn: () => api.sendEmail(draft.customer_id, { template_code: draft.template_code, subject, text }),
    onSuccess: (sent) => {
      queryClient.setQueryData(queryKeys.customerAction(draft.customer_id), sent.state)
      void queryClient.invalidateQueries({ queryKey: queryKeys.customers })
      void queryClient.invalidateQueries({ queryKey: queryKeys.customerAction(draft.customer_id) })
      onSent(sent)
    },
  })
  const empty = !subject.trim() || !text.trim()

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        if (!empty && draft.enabled) send.mutate()
      }}
      className="contents"
    >
      <div className="space-y-4">
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[13px]">
          <dt className="text-muted-foreground">Кому</dt>
          <dd className="truncate font-medium" data-testid="email-to">{draft.to}</dd>
          <dt className="text-muted-foreground">Ответ придёт</dt>
          <dd className="truncate">{draft.reply_to}</dd>
        </dl>
        {draft.last_sent_at && (
          <p className="flex items-center gap-2 rounded-md bg-risk-medium-bg px-3 py-2 text-[13px] text-risk-medium">
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
            Этому клиенту уже писали {formatDate(draft.last_sent_at)}. Проверьте, не повторяется ли письмо.
          </p>
        )}
        {!draft.enabled && (
          <p role="alert" className="rounded-md bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
            Отправка писем не настроена на сервере (нет SMTP_HOST). Текст можно посмотреть, но отправить не получится.
          </p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="text-[13px] text-muted-foreground">Шаблон</span>
            <Select value={draft.template_code} onValueChange={onTemplate} options={templateOptions} ariaLabel="Шаблон письма" className="w-60" />
          </div>
          <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="Вид">
            {(['edit', 'preview'] as const).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setView(v)}
                className={cn('h-7 rounded px-2.5 text-[13px]', view === v ? 'bg-accent font-medium' : 'text-muted-foreground')}
              >
                {v === 'edit' ? 'Текст' : 'Как увидит клиент'}
              </button>
            ))}
          </div>
        </div>
        {view === 'edit' ? (
          <>
            <div className="space-y-1.5">
              <Label htmlFor="email-subject">Тема</Label>
              <Input id="email-subject" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="email-text">Текст</Label>
              <Textarea id="email-text" value={text} onChange={(e) => setText(e.target.value)} rows={12} maxLength={10000} />
            </div>
          </>
        ) : (
          <LetterPreview from={draft.from} to={draft.to} subject={subject} text={text} />
        )}
        {send.isError && (
          <p role="alert" className="text-[13px] text-risk-high">
            {errorMessage(send.error)}
          </p>
        )}
      </div>
      <div className="-mx-6 mt-4 -mb-4 flex flex-wrap items-center justify-end gap-2 border-t border-border px-6 py-3">
        <DialogClose asChild>
          <Button variant="outline">Отмена</Button>
        </DialogClose>
        <Button type="submit" disabled={empty || !draft.enabled || send.isPending}>
          <Send />
          {send.isPending ? 'Отправляем…' : 'Отправить'}
        </Button>
      </div>
    </form>
  )
}

/** «Написать письмо»: шаблон по главной причине, правка темы и текста, отправка через SMTP. */
export function EmailDialog({
  customerId,
  email,
  open,
  onOpenChange,
  onSent,
}: {
  customerId: number
  email?: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onSent?: (sent: EmailSent) => void
}) {
  const [template, setTemplate] = useState<string | null>(null)
  const templateOptions = useTemplateOptions()
  const draft = useQuery({
    queryKey: queryKeys.emailDraft(customerId, template),
    queryFn: ({ signal }) => api.emailDraft(customerId, template, signal),
    enabled: open,
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) setTemplate(null)
        onOpenChange(next)
      }}
    >
      <DialogContent
        title={
          <span className="flex items-center gap-2">
            <Mail className="size-4 text-muted-foreground" aria-hidden />
            Письмо клиенту{email ? ` ${email}` : ''}
          </span>
        }
        description="Текст подставлен по главной причине риска. Поправьте, если нужно, и отправьте: клиент получит отметку «Письмо отправлено»."
        data-testid="email-dialog"
      >
        {draft.isPending ? (
          <div className="space-y-3" aria-busy="true">
            <Skeleton className="h-4 w-56" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-56 w-full" />
          </div>
        ) : draft.isError ? (
          <p role="alert" className="text-sm text-risk-high">
            {errorMessage(draft.error)}
          </p>
        ) : (
          <EmailForm
            key={draft.data.template_code}
            draft={draft.data}
            templateOptions={templateOptions}
            onTemplate={setTemplate}
            onSent={(sent) => {
              setTemplate(null)
              onOpenChange(false)
              onSent?.(sent)
            }}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}
