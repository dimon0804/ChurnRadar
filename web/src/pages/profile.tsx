import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, Loader2 } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { User } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ROLE_LABEL } from '@/lib/labels'
import { useAuth } from '@/providers/auth'

const METHOD_LABEL: Record<User['auth_method'], string> = {
  password: 'логин и пароль',
  oidc: 'SSO (Authentik, OIDC)',
  saml: 'SSO (Authentik, SAML)',
}

function Notice({ ok, error }: { ok: boolean; error: unknown }) {
  if (error) {
    return (
      <p role="alert" className="text-[13px] text-risk-high">
        {errorMessage(error)}
      </p>
    )
  }
  if (ok) {
    return (
      <p role="status" className="inline-flex items-center gap-1.5 text-[13px] text-risk-low">
        <Check className="size-4" />
        Сохранено
      </p>
    )
  }
  return null
}

function EmailCard({ user }: { user: User }) {
  const queryClient = useQueryClient()
  const id = useId()
  const [email, setEmail] = useState(user.email ?? '')
  const save = useMutation({
    mutationFn: (value: string | null) => api.updateProfile({ email: value }),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.me, (old: User | undefined) => (old ? { ...old, email: data.email } : old))
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    save.mutate(email.trim() || null)
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Почта</CardTitle>
        <CardDescription>На этот адрес приходят уведомления о клиентах в зоне риска.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex flex-wrap items-end gap-3" onSubmit={submit} noValidate>
          <div className="min-w-64 flex-1 space-y-1.5">
            <Label htmlFor={id}>Email</Label>
            <Input
              id={id}
              type="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value)
                save.reset()
              }}
              placeholder="name@company.ru"
              autoComplete="email"
            />
          </div>
          <Button type="submit" disabled={save.isPending || email.trim() === (user.email ?? '')}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Сохранить почту
          </Button>
        </form>
        <div className="mt-2 min-h-5">
          <Notice ok={save.isSuccess} error={save.error} />
        </div>
      </CardContent>
    </Card>
  )
}

function PasswordCard() {
  const ids = { current: useId(), next: useId(), repeat: useId() }
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [mismatch, setMismatch] = useState(false)
  const change = useMutation({
    mutationFn: () => api.changePassword({ current_password: current, new_password: next }),
    onSuccess: () => {
      setCurrent('')
      setNext('')
      setRepeat('')
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (next !== repeat) {
      setMismatch(true)
      return
    }
    setMismatch(false)
    change.mutate()
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Пароль</CardTitle>
        <CardDescription>Не короче 8 символов. После смены другие ваши сессии закроются, эта останется.</CardDescription>
      </CardHeader>
      <CardContent>
        <form className="grid max-w-md gap-3" onSubmit={submit} noValidate>
          <div className="space-y-1.5">
            <Label htmlFor={ids.current}>Текущий пароль</Label>
            <Input id={ids.current} type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.next}>Новый пароль</Label>
            <Input id={ids.next} type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor={ids.repeat}>Новый пароль ещё раз</Label>
            <Input
              id={ids.repeat}
              type="password"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value)}
              autoComplete="new-password"
              aria-invalid={mismatch || undefined}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" disabled={change.isPending || !current || !next}>
              {change.isPending && <Loader2 className="animate-spin" />}
              Сменить пароль
            </Button>
            {mismatch ? (
              <p role="alert" className="text-[13px] text-risk-high">
                Пароли не совпадают
              </p>
            ) : (
              <Notice ok={change.isSuccess} error={change.error} />
            )}
          </div>
        </form>
      </CardContent>
    </Card>
  )
}

export default function ProfilePage() {
  const { user, role: topRole } = useAuth()
  const role = ROLE_LABEL[topRole]
  return (
    <>
      <PageHeader title="Профиль" description={`${user.display_name} · ${user.username} · ${role}, вход через ${METHOD_LABEL[user.auth_method]}`} />
      <div className="grid max-w-3xl gap-4">
        <EmailCard user={user} />
        {user.auth_method === 'password' ? (
          <PasswordCard />
        ) : (
          <Card>
            <CardHeader>
              <CardTitle>Пароль</CardTitle>
              <CardDescription>
                Вы входите через Authentik, поэтому пароль меняется там, а не в панели.
              </CardDescription>
            </CardHeader>
          </Card>
        )}
      </div>
    </>
  )
}
