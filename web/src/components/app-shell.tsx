import {
  Activity,
  Bell,
  CircleUserRound,
  Database,
  FileCode2,
  LayoutDashboard,
  LogOut,
  Mail,
  Megaphone,
  Menu,
  Moon,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  UserCog,
  Users,
  Workflow,
  X,
} from 'lucide-react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { useState, type ReactNode } from 'react'
import type { Role } from '@/api/types'
import { Link, NavLink, Outlet } from 'react-router'
import { Tooltip } from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Button } from '@/components/ui/button'
import { AlertsBell } from '@/components/alerts-bell'
import { RecalcToast } from '@/components/recalc-toast'
import { formatAgo } from '@/lib/format'
import { ROLE_LABEL } from '@/lib/labels'
import { grafanaUrl, SWAGGER_PATH } from '@/lib/links'
import { cn } from '@/lib/utils'
import { useAuth } from '@/providers/auth'
import { useLive } from '@/providers/live'
import { useTheme } from '@/providers/theme'
import { useNow } from '@/hooks/use-now'

interface NavItem {
  to: string
  label: string
  icon: ReactNode
  adminOnly?: boolean
  end?: boolean
}

const ROLE_ACCESS: Record<Role, string> = {
  admin: 'полный доступ',
  marketer: 'работа с клиентами',
  analyst: 'только просмотр',
}

const NAV: NavItem[] = [
  { to: '/', label: 'Обзор', icon: <LayoutDashboard />, end: true },
  { to: '/customers', label: 'Клиенты', icon: <Users /> },
  { to: '/marketing', label: 'Маркетинг', icon: <Megaphone /> },
  { to: '/model', label: 'Модель', icon: <SlidersHorizontal />, adminOnly: true },
  { to: '/quality', label: 'Качество модели', icon: <ShieldCheck /> },
  { to: '/mail', label: 'Письма', icon: <Mail /> },
  { to: '/playbooks', label: 'Сценарии', icon: <Workflow /> },
  { to: '/data', label: 'Данные', icon: <Database />, adminOnly: true },
  { to: '/users', label: 'Пользователи', icon: <UserCog />, adminOnly: true },
]

export function Logo({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <svg viewBox="0 0 32 32" className="size-7" aria-hidden>
        <rect width="32" height="32" rx="8" className="fill-primary" />
        <circle cx="16" cy="16" r="9" fill="none" className="stroke-primary-foreground" strokeWidth="2" opacity=".45" />
        <circle cx="16" cy="16" r="4.5" fill="none" className="stroke-primary-foreground" strokeWidth="2" opacity=".7" />
        <path d="M16 16 L23.5 9.5" className="stroke-primary-foreground" strokeWidth="2.2" strokeLinecap="round" />
        <circle cx="21.5" cy="19.5" r="2.2" fill="#f0603a" />
      </svg>
      <span className="text-[15px] font-semibold tracking-[-0.02em]">ChurnRadar</span>
    </span>
  )
}

function LiveIndicator() {
  const { status, lastRecalc } = useLive()
  const now = useNow(lastRecalc ? 1000 : null)
  const online = status === 'online'
  const label = online ? 'онлайн' : status === 'connecting' ? 'подключение…' : 'переподключение…'
  const hint = online
    ? lastRecalc
      ? `Данные обновляются сами. Последний пересчёт ${formatAgo(lastRecalc.receivedAt, now)}, версия модели ${lastRecalc.modelVersion}.`
      : 'Данные обновляются сами, перезагружать страницу не нужно.'
    : 'Связь с сервером потеряна, пробуем восстановить. Данные на экране могли устареть.'

  return (
    <Tooltip content={hint} side="bottom">
      <span
        role="status"
        aria-live="polite"
        tabIndex={0}
        className={cn(
          'inline-flex h-7 items-center gap-2 rounded-full border px-2.5 text-xs font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring',
          online
            ? 'border-risk-low/25 bg-risk-low-bg text-risk-low'
            : 'border-risk-medium/30 bg-risk-medium-bg text-risk-medium',
        )}
      >
        <span className="relative flex size-2">
          {online && (
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-risk-low-bar opacity-50 motion-reduce:hidden [animation-duration:2.4s]" />
          )}
          <span className={cn('relative inline-flex size-2 rounded-full', online ? 'bg-risk-low-bar' : 'bg-risk-medium-bar')} />
        </span>
        <span className="max-md:sr-only">{label}</span>
      </span>
    </Tooltip>
  )
}

function ThemeToggle() {
  const { resolved, toggle } = useTheme()
  const label = resolved === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'
  return (
    <Tooltip content={label} side="bottom">
      <Button variant="ghost" size="icon-sm" onClick={toggle} aria-label={label} className="text-muted-foreground">
        {resolved === 'dark' ? <Sun /> : <Moon />}
      </Button>
    </Tooltip>
  )
}

function initials(name: string): string {
  const parts = name.trim().split(/[\s._@-]+/).filter(Boolean)
  const letters = parts.slice(0, 2).map((p) => p.charAt(0).toUpperCase())
  return letters.join('') || '?'
}

/** showName — подпись с именем рядом с аватаром; у администратора на неё не хватает места рядом с восемью разделами. */
function UserMenu({ showName = true }: { showName?: boolean }) {
  const { user, role: topRole, logout } = useAuth()
  const name = user.display_name?.trim() || user.username
  const role = ROLE_LABEL[topRole]
  const access = ROLE_ACCESS[topRole]
  // «Администратор / Администратор» — когда имя совпадает с ролью; вторая строка — логин и права
  const via = user.auth_method === 'oidc' ? 'SSO' : user.auth_method === 'saml' ? 'SAML' : user.username
  const caption = `${via} · ${access}`
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-9 items-center gap-2.5 rounded-md py-1 pr-2 pl-1 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Профиль: ${name === role ? name : `${name}, ${role}`}, ${caption}`}
        >
          <span className="flex size-7 items-center justify-center rounded-full bg-muted text-[11px] font-semibold text-muted-foreground">
            {initials(name)}
          </span>
          <span className={cn('hidden max-w-36 flex-col leading-tight', showName && '2xl:flex')}>
            <span className="truncate text-[13px] font-medium" title={name}>
              {name}
            </span>
            <span className="truncate text-[11px] text-muted-foreground">{caption}</span>
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>
          <p className="truncate text-[13px] font-medium" title={name}>
            {name}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {name === role ? caption : `${role} · ${caption}`}
          </p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <NavLink to="/notifications">
            <Bell />
            Уведомления
          </NavLink>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <Link to="/profile">
            <CircleUserRound />
            Профиль: почта и пароль
          </Link>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href={SWAGGER_PATH} target="_blank" rel="noopener noreferrer">
            <FileCode2 />
            API (Swagger)
          </a>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href={grafanaUrl()} target="_blank" rel="noopener noreferrer">
            <Activity />
            Мониторинг (Grafana)
          </a>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()}>
          <LogOut />
          Выйти
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** На телефоне и планшете разделы уезжают в выдвижную панель: в шапке остаются бургер, колокольчик и профиль. */
function MobileNav({ items }: { items: NavItem[] }) {
  const [open, setOpen] = useState(false)
  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Trigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="text-muted-foreground lg:hidden"
          aria-label="Открыть меню разделов"
          data-testid="nav-menu"
        >
          <Menu />
        </Button>
      </DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/30 data-[state=closed]:animate-overlay-out data-[state=open]:animate-overlay-in dark:bg-black/50" />
        <DialogPrimitive.Content
          className="fixed inset-y-0 left-0 z-50 flex w-[min(300px,85vw)] flex-col border-r border-border bg-background pb-[env(safe-area-inset-bottom)] shadow-pop outline-none data-[state=open]:animate-fade-in"
          aria-describedby={undefined}
          data-testid="nav-drawer"
        >
          <div className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
            <DialogPrimitive.Title asChild>
              <span>
                <Logo />
                <span className="sr-only">: разделы</span>
              </span>
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              className="-mr-1 inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              aria-label="Закрыть меню"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <nav aria-label="Разделы" className="min-h-0 flex-1 overflow-y-auto p-2">
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={() => setOpen(false)}
                className={({ isActive }) =>
                  cn(
                    'flex h-11 items-center gap-3 rounded-md px-3 text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-4',
                    isActive ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                  )
                }
              >
                <span className="opacity-80" aria-hidden>
                  {item.icon}
                </span>
                {item.label}
              </NavLink>
            ))}
          </nav>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

export function AppShell() {
  const { isAdmin } = useAuth()
  const items = NAV.filter((item) => !item.adminOnly || isAdmin)

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        К содержимому
      </a>
      <header className="sticky top-0 z-30 border-b border-border bg-background/85 backdrop-blur-md supports-[backdrop-filter]:bg-background/70">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-1 px-3 sm:gap-4 sm:px-4 md:px-6">
          <MobileNav items={items} />
          <NavLink to="/" className="shrink-0 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="ChurnRadar, обзор">
            <Logo className="[&>span]:max-sm:sr-only" />
          </NavLink>
          <nav aria-label="Разделы" className="flex min-w-0 items-center gap-0.5 max-lg:hidden sm:ml-2">
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                title={item.label}
                className={({ isActive }) =>
                  cn(
                    'inline-flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-[13px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring sm:px-2.5 [&_svg]:size-4',
                    isActive
                      ? 'bg-accent text-foreground'
                      : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                  )
                }
              >
                <span className="opacity-80" aria-hidden>
                  {item.icon}
                </span>
                {/* у администратора пунктов вдвое больше: подписи влезают только на широком экране */}
                <span className={items.length > 6 ? 'max-2xl:sr-only' : 'max-lg:sr-only'}>{item.label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex shrink-0 items-center gap-1 sm:gap-1.5">
            <LiveIndicator />
            <AlertsBell />
            <ThemeToggle />
            <div className="mx-1 h-5 w-px bg-border max-sm:hidden" aria-hidden />
            <UserMenu showName={items.length <= 6} />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[1400px] min-w-0 flex-1 px-4 pt-6 pb-16 md:px-6">
        <Outlet />
      </main>
      <RecalcToast />
    </div>
  )
}
