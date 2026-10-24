import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Ban,
  Check,
  Copy,
  KeyRound,
  Loader2,
  MoreHorizontal,
  Pencil,
  Search,
  ShieldCheck,
  Trash2,
  UserPlus,
  UserRoundCheck,
} from 'lucide-react'
import { useEffect, useId, useState, type FormEvent, type ReactNode } from 'react'
import { errorMessage } from '@/api/client'
import { api, queryKeys } from '@/api/endpoints'
import type { AuditEntry, ManagedUser, UserCreate } from '@/api/types'
import { PageHeader } from '@/components/page-header'
import { EmptyState, ErrorState } from '@/components/states'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog } from '@/components/ui/modal'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDateTime } from '@/lib/format'
import { ROLE_LABEL } from '@/lib/labels'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'

const SOURCE_LABEL: Record<ManagedUser['source'], string> = {
  password: 'Пароль',
  oidc: 'SSO · OIDC',
  saml: 'SSO · SAML',
  sso: 'SSO, ещё не входил',
}

const ROLE_OPTIONS = [
  { value: 'analyst', label: ROLE_LABEL.analyst },
  { value: 'admin', label: ROLE_LABEL.admin },
]

const AUDIT_LABEL: Record<string, string> = {
  'user.create': 'создал пользователя',
  'user.update': 'изменил данные',
  'user.role': 'сменил роль',
  'user.block': 'заблокировал',
  'user.unblock': 'разблокировал',
  'user.password_reset': 'сбросил пароль',
  'user.delete': 'удалил',
  'self.password': 'сменил свой пароль',
  'self.email': 'сменил свой email',
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return debounced
}

function FormError({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p role="alert" className="rounded-md border border-risk-high/20 bg-risk-high-bg px-3 py-2 text-[13px] text-risk-high">
      {errorMessage(error)}
    </p>
  )
}

function Field({ label, hint, children, id }: { label: string; hint?: ReactNode; children: ReactNode; id: string }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs leading-5 text-muted-foreground">{hint}</p>}
    </div>
  )
}

function StatusBadge({ user }: { user: ManagedUser }) {
  const active = user.status === 'active'
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center rounded-full px-2 text-xs font-medium',
        active ? 'bg-risk-low-bg text-risk-low' : 'bg-risk-high-bg text-risk-high',
      )}
    >
      {active ? 'Активен' : 'Заблокирован'}
    </span>
  )
}

/** Временный пароль показываем один раз: потом его не узнать даже админу. */
function SecretDialog({ secret, username, onClose }: { secret: string | null; username: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <Dialog
      open={secret !== null}
      onOpenChange={(open) => {
        if (!open) {
          setCopied(false)
          onClose()
        }
      }}
      title="Временный пароль"
      description={`Передайте его пользователю ${username}. Больше мы его не покажем: при необходимости пароль можно сбросить ещё раз.`}
      footer={<Button onClick={onClose}>Готово</Button>}
    >
      <div className="flex items-center gap-2">
        <code
          data-testid="temporary-password"
          className="flex-1 rounded-md border border-border bg-muted px-3 py-2 font-mono text-[15px] tracking-wide select-all"
        >
          {secret}
        </code>
        <Button
          variant="outline"
          size="icon"
          aria-label="Скопировать пароль"
          onClick={() => {
            if (secret) void navigator.clipboard?.writeText(secret).then(() => setCopied(true), () => undefined)
          }}
        >
          {copied ? <Check /> : <Copy />}
        </Button>
      </div>
    </Dialog>
  )
}

type PasswordMode = 'generate' | 'set' | 'sso'

function CreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (username: string, secret: string | null) => void
}) {
  const queryClient = useQueryClient()
  const ids = { login: useId(), name: useId(), email: useId(), password: useId(), form: useId() }
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<'admin' | 'analyst'>('analyst')
  const [mode, setMode] = useState<PasswordMode>('generate')
  const [password, setPassword] = useState('')

  const reset = () => {
    setUsername('')
    setDisplayName('')
    setEmail('')
    setRole('analyst')
    setMode('generate')
    setPassword('')
    create.reset()
  }

  const create = useMutation({
    mutationFn: (body: UserCreate) => api.createUser(body),
    onSuccess: (data) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users })
      onCreated(data.user.username, data.temporary_password ?? null)
      reset()
      onOpenChange(false)
    },
  })

  const submit = (event: FormEvent) => {
    event.preventDefault()
    create.mutate({
      username: username.trim(),
      display_name: displayName.trim(),
      email: email.trim() || null,
      role,
      password: mode === 'set' ? password : null,
      sso_only: mode === 'sso',
    })
  }

  const modes: { value: PasswordMode; label: string }[] = [
    { value: 'generate', label: 'Временный пароль' },
    { value: 'set', label: 'Задать пароль' },
    { value: 'sso', label: 'Только SSO' },
  ]

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) reset()
        onOpenChange(next)
      }}
      title="Новый пользователь"
      description="Аналитик видит всё, но ничего не меняет. Администратор управляет моделью, данными и пользователями."
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Отмена
          </Button>
          <Button type="submit" form={ids.form} disabled={create.isPending || !username.trim()}>
            {create.isPending && <Loader2 className="animate-spin" />}
            Создать
          </Button>
        </>
      }
    >
      <form id={ids.form} className="space-y-4" onSubmit={submit} noValidate>
        <FormError error={create.error} />
        <Field id={ids.login} label="Логин" hint="Латиница, цифры, точка, дефис. Для SSO — как логин в Authentik.">
          <Input
            id={ids.login}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
          />
        </Field>
        <Field id={ids.name} label="Имя">
          <Input id={ids.name} value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Как показывать в панели" />
        </Field>
        <Field id={ids.email} label="Email" hint="Сюда придут уведомления. Можно оставить пустым.">
          <Input id={ids.email} type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
        </Field>
        <div className="space-y-1.5">
          <Label>Роль</Label>
          <Select
            value={role}
            onValueChange={(v) => setRole(v as 'admin' | 'analyst')}
            options={ROLE_OPTIONS}
            ariaLabel="Роль"
            className="h-9 w-full"
          />
        </div>
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Как будет входить</legend>
          <div role="radiogroup" aria-label="Как будет входить" className="grid grid-cols-3 gap-1 rounded-lg bg-muted p-1">
            {modes.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={mode === m.value}
                onClick={() => setMode(m.value)}
                className={cn(
                  'h-8 rounded-md px-2 text-[13px] font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  mode === m.value ? 'bg-card text-foreground shadow-card' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {m.label}
              </button>
            ))}
          </div>
          <p className="text-xs leading-5 text-muted-foreground">
            {mode === 'generate' && 'Сгенерируем пароль и покажем один раз, его нужно будет передать сотруднику.'}
            {mode === 'set' && 'Не короче 8 символов. Пользователь сможет сменить его в профиле.'}
            {mode === 'sso' &&
              'Пароля не будет. При первом входе через Authentik с этим логином учётка привяжется, роль останется той, что вы выбрали.'}
          </p>
          {mode === 'set' && (
            <Field id={ids.password} label="Пароль">
              <Input
                id={ids.password}
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
              />
            </Field>
          )}
        </fieldset>
      </form>
    </Dialog>
  )
}

function EditDialog({ user, onClose, isSelf }: { user: ManagedUser | null; onClose: () => void; isSelf: boolean }) {
  // форма монтируется заново для каждого пользователя и берёт его значения как начальные
  return user ? <EditUserForm key={user.id} user={user} onClose={onClose} isSelf={isSelf} /> : null
}

function EditUserForm({ user, onClose, isSelf }: { user: ManagedUser; onClose: () => void; isSelf: boolean }) {
  const queryClient = useQueryClient()
  const ids = { name: useId(), email: useId(), status: useId(), form: useId() }
  const [displayName, setDisplayName] = useState(user.display_name)
  const [email, setEmail] = useState(user.email ?? '')
  const [role, setRole] = useState<'admin' | 'analyst'>(user.role)
  const [active, setActive] = useState(user.status === 'active')

  const save = useMutation({
    mutationFn: () => {
      return api.updateUser(user.id, {
        display_name: displayName.trim() || user.display_name,
        email: email.trim() || null,
        ...(isSelf ? {} : { role, status: active ? 'active' : 'blocked' }),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users })
      onClose()
    },
  })

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title={`Пользователь ${user.username}`}
      description={
        isSelf
          ? 'Свою роль и статус поменять нельзя, это может сделать другой администратор.'
          : 'После смены роли или блокировки пользователя выкинет из панели, войти он сможет заново (если не заблокирован).'
      }
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button type="submit" form={ids.form} disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" />}
            Сохранить
          </Button>
        </>
      }
    >
      <form
        id={ids.form}
        className="space-y-4"
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <FormError error={save.error} />
        <Field id={ids.name} label="Имя">
          <Input id={ids.name} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </Field>
        <Field id={ids.email} label="Email">
          <Input id={ids.email} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <div className="space-y-1.5">
          <Label>Роль</Label>
          {isSelf ? (
            <p className="text-sm text-muted-foreground">{user ? ROLE_LABEL[user.role] : ''}</p>
          ) : (
            <Select
              value={role}
              onValueChange={(v) => setRole(v as 'admin' | 'analyst')}
              options={ROLE_OPTIONS}
              ariaLabel="Роль"
              className="h-9 w-full"
            />
          )}
        </div>
        {!isSelf && (
          <div className="flex items-center justify-between gap-4 rounded-lg border border-border px-3.5 py-3">
            <div>
              <Label htmlFor={ids.status}>Доступ к панели</Label>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {active ? 'Может входить' : 'Заблокирован: не войдёт ни по паролю, ни через SSO'}
              </p>
            </div>
            <Switch id={ids.status} checked={active} onCheckedChange={setActive} />
          </div>
        )}
      </form>
    </Dialog>
  )
}

function ResetPasswordDialog({
  user,
  onClose,
  onGenerated,
}: {
  user: ManagedUser | null
  onClose: () => void
  onGenerated: (username: string, secret: string) => void
}) {
  const ids = { password: useId(), form: useId() }
  const [password, setPassword] = useState('')
  const [done, setDone] = useState(false)
  const reset = useMutation({
    mutationFn: (value: string | null) => {
      if (!user) throw new Error('no user')
      return api.resetUserPassword(user.id, value)
    },
    onSuccess: (data) => {
      if (data?.temporary_password && user) {
        onGenerated(user.username, data.temporary_password)
        close()
      } else {
        setDone(true)
      }
    },
  })
  const close = () => {
    setPassword('')
    setDone(false)
    reset.reset()
    onClose()
  }
  return (
    <Dialog
      open={user !== null}
      onOpenChange={(open) => !open && close()}
      title={user ? `Пароль для ${user.username}` : ''}
      description="Старый пароль перестанет работать, а открытые сессии пользователя закроются."
      footer={
        done ? (
          <Button onClick={close}>Готово</Button>
        ) : (
          <>
            <Button variant="outline" disabled={reset.isPending} onClick={() => reset.mutate(null)}>
              Сгенерировать временный
            </Button>
            <Button type="submit" form={ids.form} disabled={reset.isPending || password.length === 0}>
              {reset.isPending && <Loader2 className="animate-spin" />}
              Задать этот
            </Button>
          </>
        )
      }
    >
      {done ? (
        <p className="text-sm">Пароль изменён. Сообщите его пользователю.</p>
      ) : (
        <form
          id={ids.form}
          className="space-y-3"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            reset.mutate(password)
          }}
        >
          <FormError error={reset.error} />
          <Field id={ids.password} label="Новый пароль" hint="Не короче 8 символов. Или сгенерируйте временный.">
            <Input
              id={ids.password}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="new-password"
            />
          </Field>
        </form>
      )}
    </Dialog>
  )
}

function DeleteDialog({ user, onClose }: { user: ManagedUser | null; onClose: () => void }) {
  const queryClient = useQueryClient()
  const remove = useMutation({
    mutationFn: () => {
      if (!user) throw new Error('no user')
      return api.deleteUser(user.id)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.users })
      onClose()
    },
  })
  const sso = user && user.source !== 'password'
  return (
    <Dialog
      open={user !== null}
      onOpenChange={(open) => {
        if (!open) {
          remove.reset()
          onClose()
        }
      }}
      title={user ? `Удалить ${user.username}?` : ''}
      description={
        sso
          ? 'Учётка исчезнет из панели. Если сотрудник остался в группе ChurnRadar в Authentik, при следующем входе она создастся заново. Чтобы закрыть доступ, лучше заблокировать.'
          : 'Учётка и её сессии будут удалены. Действие попадёт в журнал, отменить его нельзя.'
      }
      footer={
        <>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button variant="destructive" disabled={remove.isPending} onClick={() => remove.mutate()}>
            {remove.isPending && <Loader2 className="animate-spin" />}
            Удалить
          </Button>
        </>
      }
    >
      <FormError error={remove.error} />
    </Dialog>
  )
}

function AuditLog() {
  const audit = useQuery({ queryKey: queryKeys.userAudit, queryFn: ({ signal }) => api.userAudit(30, signal) })
  const items: AuditEntry[] = audit.data?.items ?? []
  return (
    <Card className="mt-6">
      <div className="border-b border-border px-5 py-3.5">
        <h2 className="text-sm font-semibold">Журнал действий</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">Кто и когда менял пользователей. Последние 30 записей.</p>
      </div>
      {audit.isPending ? (
        <div className="space-y-2 p-5">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-5 w-1/2" />
        </div>
      ) : audit.isError ? (
        <ErrorState error={audit.error} onRetry={() => void audit.refetch()} />
      ) : items.length === 0 ? (
        <p className="px-5 py-6 text-[13px] text-muted-foreground">Пока ничего не меняли.</p>
      ) : (
        <ul className="divide-y divide-border">
          {items.map((e) => (
            <li key={e.id} className="flex flex-wrap items-baseline gap-x-2 px-5 py-2.5 text-[13px]">
              <span className="w-28 shrink-0 text-xs text-muted-foreground num">{formatDateTime(e.created_at)}</span>
              <span className="font-medium">{e.actor}</span>
              <span className="text-muted-foreground">{AUDIT_LABEL[e.action] ?? e.action}</span>
              {e.target && e.target !== e.actor && <span className="font-medium">{e.target}</span>}
              {e.action === 'user.role' && typeof e.details.role === 'string' && (
                <span className="text-muted-foreground">→ {ROLE_LABEL[e.details.role as 'admin' | 'analyst'] ?? e.details.role}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

export default function UsersPage() {
  const { user: me } = useAuth()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const q = useDebounced(search.trim(), 250)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState<ManagedUser | null>(null)
  const [resetting, setResetting] = useState<ManagedUser | null>(null)
  const [deleting, setDeleting] = useState<ManagedUser | null>(null)
  const [secret, setSecret] = useState<{ username: string; value: string } | null>(null)

  const users = useQuery({
    queryKey: queryKeys.userList(q),
    queryFn: ({ signal }) => api.users(q, signal),
    placeholderData: keepPreviousData,
  })

  const toggle = useMutation({
    mutationFn: (u: ManagedUser) => api.updateUser(u.id, { status: u.status === 'active' ? 'blocked' : 'active' }),
    onSettled: () => void queryClient.invalidateQueries({ queryKey: queryKeys.users }),
  })

  const items = users.data?.items ?? []

  return (
    <>
      <PageHeader
        title="Пользователи"
        description="Кто может войти в панель и что ему можно. Сотрудники из Authentik появляются здесь сами после первого входа."
        actions={
          <Button onClick={() => setCreating(true)}>
            <UserPlus />
            Добавить пользователя
          </Button>
        }
      />

      <Card>
        <div className="flex flex-wrap items-center gap-3 border-b border-border px-5 py-3">
          <div className="relative w-full max-w-xs">
            <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Логин, имя или email"
              aria-label="Поиск пользователей"
              className="h-8 pl-8"
            />
          </div>
          {toggle.isError && <p className="text-[13px] text-risk-high">{errorMessage(toggle.error)}</p>}
          <p className="ml-auto text-xs text-muted-foreground num">
            {users.data ? `Всего: ${users.data.total}` : ''}
          </p>
        </div>

        {users.isPending ? (
          <div className="space-y-2 p-5">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : users.isError ? (
          <ErrorState error={users.error} onRetry={() => void users.refetch()} />
        ) : items.length === 0 ? (
          <EmptyState title="Никого не нашли" description="Попробуйте другой запрос или добавьте пользователя." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Пользователь</TableHead>
                <TableHead>Роль</TableHead>
                <TableHead>Вход</TableHead>
                <TableHead>Статус</TableHead>
                <TableHead>Последний вход</TableHead>
                <TableHead className="w-12">
                  <span className="sr-only">Действия</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.map((u) => {
                const isSelf = u.id === me.id
                return (
                  <TableRow key={u.id} data-testid={`user-row-${u.username}`} className={cn(u.status === 'blocked' && 'opacity-70')}>
                    <TableCell>
                      <div className="min-w-0">
                        <p className="truncate font-medium">
                          {u.display_name}
                          {isSelf && <span className="ml-1.5 text-xs font-normal text-muted-foreground">(это вы)</span>}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {u.username}
                          {u.email ? ` · ${u.email}` : ''}
                        </p>
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className="inline-flex items-center gap-1.5 text-[13px]">
                        {u.role === 'admin' && <ShieldCheck className="size-3.5 text-primary" />}
                        {ROLE_LABEL[u.role]}
                      </span>
                    </TableCell>
                    <TableCell className="text-[13px] text-muted-foreground">{SOURCE_LABEL[u.source]}</TableCell>
                    <TableCell>
                      <StatusBadge user={u} />
                    </TableCell>
                    <TableCell className="text-[13px] text-muted-foreground num">
                      {u.last_login_at ? formatDateTime(u.last_login_at) : 'ещё не входил'}
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon-sm" aria-label={`Действия: ${u.username}`}>
                            <MoreHorizontal />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onSelect={() => setEditing(u)}>
                            <Pencil />
                            Изменить
                          </DropdownMenuItem>
                          {u.source === 'password' && (
                            <DropdownMenuItem onSelect={() => setResetting(u)}>
                              <KeyRound />
                              Сбросить пароль
                            </DropdownMenuItem>
                          )}
                          {!isSelf && (
                            <DropdownMenuItem onSelect={() => toggle.mutate(u)}>
                              {u.status === 'active' ? <Ban /> : <UserRoundCheck />}
                              {u.status === 'active' ? 'Заблокировать' : 'Разблокировать'}
                            </DropdownMenuItem>
                          )}
                          {!isSelf && (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem onSelect={() => setDeleting(u)} className="text-risk-high">
                                <Trash2 />
                                Удалить
                              </DropdownMenuItem>
                            </>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                )
              })}
            </TableBody>
          </Table>
        )}
      </Card>

      <AuditLog />

      <CreateDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(username, value) => {
          void queryClient.invalidateQueries({ queryKey: queryKeys.userAudit })
          if (value) setSecret({ username, value })
        }}
      />
      <EditDialog user={editing} isSelf={editing?.id === me.id} onClose={() => setEditing(null)} />
      <ResetPasswordDialog
        user={resetting}
        onClose={() => setResetting(null)}
        onGenerated={(username, value) => setSecret({ username, value })}
      />
      <DeleteDialog user={deleting} onClose={() => setDeleting(null)} />
      <SecretDialog secret={secret?.value ?? null} username={secret?.username ?? ''} onClose={() => setSecret(null)} />
    </>
  )
}
