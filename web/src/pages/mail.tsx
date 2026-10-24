import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, Mail, RotateCcw, Save } from 'lucide-react'
import { useRef, useState } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { MailTemplate } from '@/api/types'
import { campaignResult } from '@/components/campaign-dialog'
import { LetterPreview } from '@/components/email-dialog'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { statsLine, TEMPLATE_TITLE } from '@/lib/contact-today'
import { formatDateTime } from '@/lib/format'
import { insertAt, renderTemplate, SAMPLE_VALUES, unknownPlaceholders } from '@/lib/mail'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'

function TemplateEditor({
  template,
  placeholders,
  canEdit,
}: {
  template: MailTemplate
  placeholders: { name: string; description: string }[]
  canEdit: boolean
}) {
  const queryClient = useQueryClient()
  const [subject, setSubject] = useState(template.subject)
  const [body, setBody] = useState(template.body)
  const bodyRef = useRef<HTMLTextAreaElement>(null)
  const names = placeholders.map((p) => p.name)
  const bad = unknownPlaceholders(`${subject}\n${body}`, names)
  const dirty = subject !== template.subject || body !== template.body

  const onSaved = (saved: MailTemplate) => {
    queryClient.setQueryData(queryKeys.mailTemplates, (old: { items: MailTemplate[] } | undefined) =>
      old ? { ...old, items: old.items.map((t) => (t.code === saved.code ? saved : t)) } : old,
    )
    setSubject(saved.subject)
    setBody(saved.body)
  }
  const save = useMutation({ mutationFn: () => api.putMailTemplate(template.code, { subject, body }), onSuccess: onSaved })
  const reset = useMutation({ mutationFn: () => api.resetMailTemplate(template.code), onSuccess: onSaved })

  const insert = (name: string) => {
    const el = bodyRef.current
    const next = insertAt(body, el?.selectionStart ?? body.length, el?.selectionEnd ?? body.length, `{{${name}}}`)
    setBody(next.text)
    requestAnimationFrame(() => {
      el?.focus()
      el?.setSelectionRange(next.cursor, next.cursor)
    })
  }

  return (
    <div className="grid gap-5 xl:grid-cols-2">
      <div className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="tpl-subject">Тема</Label>
          <Input id="tpl-subject" value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={200} disabled={!canEdit} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="tpl-body">Текст</Label>
          <Textarea id="tpl-body" ref={bodyRef} value={body} onChange={(e) => setBody(e.target.value)} rows={14} maxLength={10000} disabled={!canEdit} />
        </div>
        {canEdit && (
          <div>
            <p className="mb-1.5 text-[13px] text-muted-foreground">Подстановки — нажмите, чтобы вставить в текст:</p>
            <div className="flex flex-wrap gap-1.5">
              {placeholders.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  title={p.description}
                  onClick={() => insert(p.name)}
                  className="rounded-md border border-border bg-muted/50 px-2 py-0.5 font-mono text-xs hover:bg-accent"
                >
                  {`{{${p.name}}}`}
                </button>
              ))}
            </div>
          </div>
        )}
        {bad.length > 0 && (
          <p role="alert" className="text-[13px] text-risk-high">
            Таких подстановок нет: {bad.map((b) => `{{${b}}}`).join(', ')}
          </p>
        )}
        {(save.isError || reset.isError) && (
          <p role="alert" className="text-[13px] text-risk-high">
            {errorMessage(save.error ?? reset.error)}
          </p>
        )}
        {canEdit ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => save.mutate()} disabled={!dirty || bad.length > 0 || !subject.trim() || !body.trim() || save.isPending}>
              <Save />
              {save.isPending ? 'Сохраняем…' : 'Сохранить'}
            </Button>
            {!template.is_default && (
              <Button variant="ghost" onClick={() => reset.mutate()} disabled={reset.isPending}>
                <RotateCcw />
                Вернуть исходный текст
              </Button>
            )}
            {template.updated_by && (
              <span className="text-xs text-subtle-foreground">
                Изменил {template.updated_by}, {formatDateTime(template.updated_at)}
              </span>
            )}
          </div>
        ) : (
          <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <Lock className="size-3.5" aria-hidden />
            Менять шаблоны может администратор.
          </p>
        )}
      </div>
      <div className="space-y-1.5">
        <p className="text-[13px] text-muted-foreground">Так письмо увидит клиент (пример с данными клиента 9)</p>
        <LetterPreview
          to={SAMPLE_VALUES.email ?? ''}
          subject={renderTemplate(subject, SAMPLE_VALUES)}
          text={renderTemplate(body, SAMPLE_VALUES)}
        />
      </div>
    </div>
  )
}

function Campaigns() {
  const campaigns = useQuery({ queryKey: queryKeys.campaigns, queryFn: ({ signal }) => api.campaigns(signal) })
  const items = campaigns.data?.items ?? []
  return (
    <Card>
      <CardHeader>
        <CardTitle>Рассылки</CardTitle>
        <CardDescription>Последние массовые отправки: кто запустил, по какому шаблону и чем закончилось</CardDescription>
      </CardHeader>
      <CardContent>
        {campaigns.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : campaigns.isError ? (
          <ErrorState error={campaigns.error} onRetry={() => void campaigns.refetch()} className="py-6" />
        ) : items.length === 0 ? (
          <EmptyState icon={<Mail />} title="Рассылок ещё не было" description="Запустить рассылку можно на обзоре или выбрав клиентов в таблице." className="py-6" />
        ) : (
          <Table aria-label="Рассылки">
            <TableHeader>
              <TableRow>
                <TableHead>Когда</TableHead>
                <TableHead>Кто</TableHead>
                <TableHead>Шаблон</TableHead>
                <TableHead>Итог</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="text-[13px] whitespace-nowrap num">{formatDateTime(c.created_at)}</TableCell>
                  <TableCell className="text-[13px]">{c.created_by ?? '—'}</TableCell>
                  <TableCell className="text-[13px]">{c.template_code ? (TEMPLATE_TITLE[c.template_code] ?? c.template_code) : 'По причине клиента'}</TableCell>
                  <TableCell className="text-[13px]">{c.status === 'done' ? campaignResult(c) : `Отправляется: ${c.sent} из ${c.total - c.skipped}`}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

export default function MailPage() {
  const { isAdmin } = useAuth()
  const templates = useQuery({ queryKey: queryKeys.mailTemplates, queryFn: ({ signal }) => api.mailTemplates(signal) })
  const stats = useQuery({ queryKey: queryKeys.mailStats, queryFn: ({ signal }) => api.mailStats(signal) })
  const [code, setCode] = useState<string | null>(null)
  const items = templates.data?.items ?? []
  const current = items.find((t) => t.code === code) ?? items[0]

  return (
    <>
      <PageHeader
        title="Письма"
        description="Шаблоны писем клиентам под каждую причину риска и последние рассылки."
        meta={stats.data && <span className="text-[13px] text-muted-foreground">{statsLine(stats.data) ?? 'Писем клиентам пока не отправляли'}</span>}
      />
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle>Шаблоны</CardTitle>
            <CardDescription>
              При «Написать письмо» панель берёт шаблон по главной причине риска клиента и подставляет его данные. Перед
              отправкой текст можно поправить.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {templates.isPending ? (
              <Skeleton className="h-80 w-full" />
            ) : templates.isError ? (
              <ErrorState error={templates.error} onRetry={() => void templates.refetch()} />
            ) : current ? (
              <div className="grid gap-5 lg:grid-cols-[220px_1fr]">
                <nav aria-label="Шаблоны" className="flex flex-row flex-wrap gap-1 lg:flex-col">
                  {items.map((t) => (
                    <button
                      key={t.code}
                      type="button"
                      onClick={() => setCode(t.code)}
                      aria-current={t.code === current.code ? 'true' : undefined}
                      className={cn(
                        'rounded-md px-2.5 py-1.5 text-left text-[13px] outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring',
                        t.code === current.code && 'bg-accent font-medium',
                      )}
                    >
                      {t.title}
                      {!t.is_default && <span className="ml-1.5 text-xs text-subtle-foreground">изменён</span>}
                    </button>
                  ))}
                </nav>
                <TemplateEditor
                  key={`${current.code}-${current.updated_at}`}
                  template={current}
                  placeholders={templates.data.placeholders}
                  canEdit={isAdmin}
                />
              </div>
            ) : null}
          </CardContent>
        </Card>
        <Campaigns />
      </div>
    </>
  )
}
