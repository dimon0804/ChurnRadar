import {
  Activity,
  Bell,
  ChevronDown,
  CircleUserRound,
  Database,
  FileCode2,
  LayoutDashboard,
  LogIn,
  LogOut,
  Mail,
  Megaphone,
  Menu,
  Moon,
  Plug,
  ScrollText,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sun,
  UserCog,
  Users,
  Workflow,
  X,
} from 'lucide-react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import type { Role } from '@/api/types'
import { Link, NavLink, Outlet, useLocation } from 'react-router'
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
import { BrandLogo } from '@/components/brand-logo'
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
  /** короткое пояснение под пунктом выпадающего меню */
  hint?: string
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
]

const ADMIN_GROUP = 'Администрирование'

/** Служебные разделы администратора: в шапке — одно выпадающее меню, в бургере — отдельная группа. */
const ADMIN_NAV: NavItem[] = [
  { to: '/data', label: 'Данные', hint: 'загрузка снимка и источники', icon: <Database /> },
  { to: '/users', label: 'Пользователи', hint: 'учётные записи и роли', icon: <UserCog /> },
  { to: '/login-settings', label: 'Вход и регистрация', hint: 'пароль, SSO и автосоздание', icon: <LogIn /> },
  { to: '/integrations', label: 'Интеграции', hint: 'токены API и отчёт на почту', icon: <Plug /> },
  { to: '/audit', label: 'Журнал действий', hint: 'кто, что и когда менял', icon: <ScrollText /> },
]

function isActivePath(pathname: string, item: NavItem) {
  return item.end ? pathname === item.to : pathname === item.to || pathname.startsWith(`${item.to}/`)
}

/** Квадратные кнопки-иконки шапки (40px, скругление 11px), как в макете. */
export const HEADER_ICON_BTN =
  'size-10 rounded-[11px] border border-border bg-card text-foreground hover:border-brand/60 hover:bg-brand-soft [&_svg]:size-[17px]'

/**
 * Логотип с подписью для экранного диктора. responsive — в шапке: на телефоне только знак,
 * шире — полный логотип. Высота задаётся классом (по умолчанию 40px).
 */
export function Logo({ className, responsive = false }: { className?: string; responsive?: boolean }) {
  return (
    <span className={cn('inline-flex h-10 items-center text-foreground', className)}>
      <BrandLogo className={cn('h-full', responsive && 'max-sm:hidden')} />
      {responsive && <BrandLogo variant="mark" className="h-[34px] sm:hidden" />}
      <span className="sr-only">ChurnRadar</span>
    </span>
  )
}

function LiveIndicator() {
  const { status, lastRecalc } = useLive()
  const now = useNow(lastRecalc ? 1000 : null)
  const online = status === 'online'
  const label = online ? 'Онлайн' : status === 'connecting' ? 'Подключение…' : 'Переподключение…'
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
          'inline-flex h-[30px] items-center gap-[7px] rounded-full px-2.5 text-xs font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring max-md:px-2',
          online ? 'bg-brand-soft text-risk-low' : 'bg-risk-medium-bg text-risk-medium',
        )}
      >
        <span className="relative flex size-[7px]">
          {online && (
            <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-40 motion-reduce:hidden [animation-duration:2.4s]" />
          )}
          <span className="online-dot relative inline-flex size-[7px] rounded-full bg-current" />
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
      <Button variant="ghost" size="icon" onClick={toggle} aria-label={label} className={HEADER_ICON_BTN}>
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

/** showName — подпись с именем рядом с аватаром; у администратора на неё не хватает места рядом с разделами. */
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
          className="group flex h-10 items-center gap-[9px] rounded-[11px] border border-border bg-card py-1 pr-2.5 pl-[5px] text-left transition-colors outline-none hover:border-brand/60 hover:bg-brand-soft focus-visible:ring-2 focus-visible:ring-ring data-[state=open]:border-brand/60 max-sm:pr-[5px]"
          aria-label={`Профиль: ${name === role ? name : `${name}, ${role}`}, ${caption}`}
        >
          <span className="flex size-[29px] items-center justify-center rounded-[9px] bg-surface-2 text-[11px] font-extrabold text-foreground">
            {initials(name)}
          </span>
          <span className={cn('hidden max-w-36 flex-col leading-tight', showName && '2xl:flex')}>
            <span className="truncate text-xs font-bold" title={name}>
              {name}
            </span>
            <span className="truncate text-[11px] text-muted-foreground">{caption}</span>
          </span>
          <ChevronDown
            className="size-[15px] text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180 max-sm:hidden"
            aria-hidden
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>
          <p className="truncate text-[13px] font-bold" title={name}>
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
function MobileNavLink({ item, onNavigate }: { item: NavItem; onNavigate: () => void }) {
  return (
    <NavLink
      to={item.to}
      end={item.end}
      onClick={onNavigate}
      className={({ isActive }) =>
        cn(
          'flex h-11 items-center gap-3 rounded-[10px] border px-3 text-sm font-[650] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-4',
          isActive
            ? 'border-brand/60 bg-brand-soft text-foreground [&_svg]:text-brand'
            : 'border-transparent text-muted-foreground hover:bg-surface-2 hover:text-foreground',
        )
      }
    >
      <span aria-hidden>{item.icon}</span>
      {item.label}
    </NavLink>
  )
}

function MobileNav({ items, adminItems }: { items: NavItem[]; adminItems: NavItem[] }) {
  const [open, setOpen] = useState(false)
  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Trigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className={cn(HEADER_ICON_BTN, 'lg:hidden')}
          aria-label="Открыть меню разделов"
          data-testid="nav-menu"
        >
          <Menu />
        </Button>
      </DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-[var(--overlay)] backdrop-blur-[7px] data-[state=closed]:animate-overlay-out data-[state=open]:animate-overlay-in" />
        <DialogPrimitive.Content
          className="fixed inset-y-0 left-0 z-50 flex w-[min(300px,85vw)] flex-col border-r border-border bg-background pb-[env(safe-area-inset-bottom)] shadow-pop outline-none data-[state=open]:animate-drawer-in"
          aria-describedby={undefined}
          data-testid="nav-drawer"
        >
          <div className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-border px-4">
            <DialogPrimitive.Title asChild>
              <span>
                <Logo />
                <span className="sr-only">: разделы</span>
              </span>
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              className="-mr-1 inline-flex size-9 items-center justify-center rounded-[11px] border border-border bg-card text-muted-foreground transition-colors hover:border-brand/60 hover:bg-brand-soft hover:text-foreground"
              aria-label="Закрыть меню"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <nav aria-label="Разделы" className="min-h-0 flex-1 overflow-y-auto p-3">
            <div className="space-y-1">
              {items.map((item) => (
                <MobileNavLink key={item.to} item={item} onNavigate={() => setOpen(false)} />
              ))}
            </div>
            {adminItems.length > 0 && (
              <div role="group" aria-labelledby="nav-admin-title" className="mt-3 space-y-1 border-t border-border pt-3">
                <p id="nav-admin-title" className="px-3 pb-1 text-[11px] font-bold tracking-[0.06em] text-muted-foreground uppercase">
                  {ADMIN_GROUP}
                </p>
                {adminItems.map((item) => (
                  <MobileNavLink key={item.to} item={item} onNavigate={() => setOpen(false)} />
                ))}
              </div>
            )}
          </nav>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

/** Последний пункт «таблетки»: служебные разделы администратора одним выпадающим меню. */
function AdminMenu({ items }: { items: NavItem[] }) {
  const { pathname } = useLocation()
  const current = items.find((item) => isActivePath(pathname, item))
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          title={current ? `${ADMIN_GROUP}: ${current.label}` : ADMIN_GROUP}
          aria-label={current ? `${ADMIN_GROUP}, открыт раздел «${current.label}»` : ADMIN_GROUP}
          data-nav-active={current ? '' : undefined}
          data-testid="nav-admin"
          className={cn(
            'group relative inline-flex h-[38px] shrink-0 items-center gap-1.5 rounded-[10px] px-2.5 text-sm font-[650] whitespace-nowrap transition-colors duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring',
            current ? 'text-foreground' : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
            'data-[state=open]:text-foreground',
            !current && 'data-[state=open]:bg-surface-2',
          )}
        >
          <Settings2 aria-hidden className={cn('size-4', current && 'text-brand')} />
          {/* подпись группы — только на очень широком экране: место в первую очередь у основных разделов */}
          <span className="max-[1536px]:sr-only">{ADMIN_GROUP}</span>
          <ChevronDown
            aria-hidden
            className="size-3.5 text-muted-foreground transition-transform duration-200 group-data-[state=open]:rotate-180"
          />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="min-w-64">
        <DropdownMenuLabel className="text-[11px] font-bold tracking-[0.06em] text-muted-foreground uppercase">{ADMIN_GROUP}</DropdownMenuLabel>
        {items.map((item) => {
          const active = isActivePath(pathname, item)
          return (
            <DropdownMenuItem
              key={item.to}
              asChild
              className={cn('h-auto min-h-9 py-1.5', active && 'bg-brand-soft [&_svg]:text-brand')}
            >
              <NavLink to={item.to} end={item.end}>
                {item.icon}
                <span className="flex min-w-0 flex-col leading-tight">
                  <span className={cn(active && 'font-semibold')}>{item.label}</span>
                  {item.hint && <span className="text-xs text-muted-foreground">{item.hint}</span>}
                </span>
              </NavLink>
            </DropdownMenuItem>
          )
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** Навигация-«таблетка» с плавно скользящей подложкой под активным разделом. */
function DesktopNav({ items, adminItems }: { items: NavItem[]; adminItems: NavItem[] }) {
  const navRef = useRef<HTMLElement>(null)
  const indicatorRef = useRef<HTMLSpanElement>(null)
  const { pathname } = useLocation()
  // у администратора рядом ещё «Модель» и меню администрирования: иконки рядом с подписями не помещаются
  const many = adminItems.length > 0

  useLayoutEffect(() => {
    const nav = navRef.current
    const indicator = indicatorRef.current
    if (!nav || !indicator) return
    const place = () => {
      const active = nav.querySelector<HTMLElement>('a[aria-current="page"], [data-nav-active]')
      if (!active || active.offsetWidth === 0) {
        indicator.style.opacity = '0'
        return
      }
      indicator.style.opacity = '1'
      indicator.style.left = `${active.offsetLeft}px`
      indicator.style.width = `${active.offsetWidth}px`
    }
    place()
    const ro = new ResizeObserver(place)
    ro.observe(nav)
    return () => ro.disconnect()
  }, [pathname, items.length, adminItems.length])

  return (
    <nav
      ref={navRef}
      aria-label="Разделы"
      className="relative isolate flex min-w-0 items-center gap-[5px] rounded-[15px] border border-border bg-card p-[5px] max-lg:hidden"
    >
      <span
        ref={indicatorRef}
        aria-hidden
        className="nav-pill-indicator pointer-events-none absolute top-[5px] -z-10 h-[calc(100%-10px)] rounded-[10px] bg-brand-soft opacity-0"
        style={{ left: 5, width: 40 }}
      />
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          title={item.label}
          className={({ isActive }) =>
            cn(
              'nav-pill-link relative inline-flex h-[38px] shrink-0 items-center gap-2 rounded-[10px] px-[11px] text-sm font-[650] whitespace-nowrap transition-colors duration-200 outline-none focus-visible:ring-2 focus-visible:ring-ring [&_svg]:size-4',
              isActive ? 'text-foreground [&_svg]:text-brand' : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
              many ? 'xl:px-2.5' : 'min-[1440px]:px-[13px]',
            )
          }
        >
          {/* узко — только иконки; от 1280 — подписи; совсем широко (и разделов немного) — и то и другое */}
          <span aria-hidden className={many ? 'xl:hidden' : 'xl:max-[1440px]:hidden'}>
            {item.icon}
          </span>
          <span className="max-xl:sr-only">{item.label}</span>
        </NavLink>
      ))}
      {adminItems.length > 0 && (
        <>
          <span aria-hidden className="mx-0.5 h-5 w-px shrink-0 bg-border" />
          <AdminMenu items={adminItems} />
        </>
      )}
    </nav>
  )
}

export function AppShell() {
  const { isAdmin } = useAuth()
  const items = NAV.filter((item) => !item.adminOnly || isAdmin)
  const adminItems = isAdmin ? ADMIN_NAV : []

  return (
    <div className="flex min-h-screen flex-col">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-primary px-3 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        К содержимому
      </a>
      <header className="sticky top-0 z-30 border-b border-border bg-[color-mix(in_srgb,var(--background)_88%,transparent)] backdrop-blur-[18px]">
        <div className="mx-auto flex h-16 max-w-[1440px] items-center gap-2 px-3.5 sm:gap-3 md:px-6 lg:h-[70px] lg:gap-[22px] xl:px-7">
          <MobileNav items={items} adminItems={adminItems} />
          <NavLink to="/" className="shrink-0 rounded-[11px] outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="ChurnRadar, обзор">
            <Logo responsive className="h-10 lg:h-12" />
          </NavLink>
          <div className="flex min-w-0 flex-1 justify-center max-lg:hidden">
            <DesktopNav items={items} adminItems={adminItems} />
          </div>
          <div className="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2.5">
            <LiveIndicator />
            <AlertsBell />
            <ThemeToggle />
            <UserMenu showName={adminItems.length === 0} />
          </div>
        </div>
      </header>
      <main id="main" className="relative z-[1] mx-auto w-full max-w-[1440px] min-w-0 flex-1 px-4 pt-6 pb-16 md:px-6 lg:pt-8 xl:px-7">
        <Outlet />
      </main>
      <RecalcToast />
    </div>
  )
}
