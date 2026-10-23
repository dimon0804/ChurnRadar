import { Database, LayoutDashboard, LogOut, Moon, SlidersHorizontal, Sun, Users } from 'lucide-react'
import type { ReactNode } from 'react'
import { NavLink, Outlet } from 'react-router'
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
import { formatAgo } from '@/lib/format'
import { ROLE_LABEL } from '@/lib/labels'
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

const NAV: NavItem[] = [
  { to: '/', label: 'Обзор', icon: <LayoutDashboard />, end: true },
  { to: '/customers', label: 'Клиенты', icon: <Users /> },
  { to: '/model', label: 'Модель', icon: <SlidersHorizontal />, adminOnly: true },
  { to: '/data', label: 'Данные', icon: <Database />, adminOnly: true },
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

function UserMenu() {
  const { user, logout } = useAuth()
  const name = user.display_name?.trim() || user.username
  const role = user.roles.includes('admin') ? ROLE_LABEL.admin : ROLE_LABEL.analyst
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className="flex h-9 items-center gap-2.5 rounded-md py-1 pr-2 pl-1 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={`Профиль: ${name}, ${role}`}
        >
          <span className="flex size-7 items-center justify-center rounded-full bg-muted text-[11px] font-semibold text-muted-foreground">
            {initials(name)}
          </span>
          <span className="hidden max-w-36 flex-col leading-tight lg:flex">
            <span className="truncate text-[13px] font-medium" title={name}>
              {name}
            </span>
            <span className="text-[11px] text-muted-foreground">{role}</span>
          </span>
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>
          <p className="truncate text-[13px] font-medium" title={name}>
            {name}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {role} · {user.auth_method === 'oidc' ? 'вход через SSO' : user.username}
          </p>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()}>
          <LogOut />
          Выйти
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
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
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4 md:px-6">
          <NavLink to="/" className="rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="ChurnRadar, обзор">
            <Logo />
          </NavLink>
          <nav aria-label="Разделы" className="ml-2 flex items-center gap-0.5">
            {items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(
                    'inline-flex h-8 items-center gap-2 rounded-md px-2.5 text-[13px] font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-4',
                    isActive
                      ? 'bg-accent text-foreground'
                      : 'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
                  )
                }
              >
                <span className="opacity-80">{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-1.5">
            <LiveIndicator />
            <ThemeToggle />
            <div className="mx-1 h-5 w-px bg-border" aria-hidden />
            <UserMenu />
          </div>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 pt-6 pb-16 md:px-6">
        <Outlet />
      </main>
    </div>
  )
}
