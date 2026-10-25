/** HTML-слайды для видео: открываются как data:-страницы, поэтому всё внутри, без внешних файлов. */

const BASE_CSS = `
  * { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { width: 100%; height: 100%; }
  body { background: radial-gradient(1200px 700px at 20% 0%, #1c2433 0%, #0d1118 60%, #090c12 100%);
    color: #e9edf5; font-family: "Segoe UI", Inter, system-ui, Arial, sans-serif; overflow: hidden; }
  .wrap { position: absolute; inset: 0; padding: 56px 72px 170px; display: flex; flex-direction: column; }
  .kicker { color: #ffc53d; font-size: 18px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; }
  h1 { font-size: 46px; font-weight: 700; letter-spacing: -.02em; margin-top: 10px; }
  .fade { opacity: 0; transform: translateY(10px); animation: in .7s ease forwards; }
  @keyframes in { to { opacity: 1; transform: none; } }
`

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

export function dataUrl(html: string): string {
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`
}

export function titleSlide(baseUrl: string): string {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>ChurnRadar</title><style>${BASE_CSS}
  .center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding-bottom: 120px; }
  .logo { font-size: 84px; font-weight: 800; letter-spacing: -.03em; }
  .logo span { color: #ffc53d; }
  .sub { margin-top: 18px; font-size: 28px; color: #b9c2d3; }
  .url { margin-top: 28px; font-size: 18px; color: #7f8aa0; font-family: Consolas, monospace; }
  </style></head><body><div class="center">
    <div class="logo fade">Churn<span>Radar</span></div>
    <div class="sub fade" style="animation-delay:.3s">Анализатор рисков оттока клиентов хостинга</div>
    <div class="url fade" style="animation-delay:.6s">${esc(baseUrl)}</div>
  </div></body></html>`
}

export function terminalSlide(command: string, lines: string[], note: string): string {
  const body = lines.map((l, i) => {
    const m = l.match(/^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+(.*)$/)
    const html = m
      ? `<span class="n">${esc(m[1]!)}</span><span class="l">${esc(m[2] ?? '')}</span> <span class="v">${esc(m[3]!)}</span>`
      : `<span class="c">${esc(l)}</span>`
    return `<div class="ln" style="animation-delay:${(1.6 + i * 0.12).toFixed(2)}s">${html}</div>`
  }).join('')
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>/metrics</title><style>${BASE_CSS}
  .term { margin-top: 26px; flex: 1; min-height: 0; border-radius: 16px; background: #0a0d13; border: 1px solid #263044;
    box-shadow: 0 20px 60px rgba(0,0,0,.45); overflow: hidden; display: flex; flex-direction: column; }
  .bar { height: 38px; display: flex; align-items: center; gap: 8px; padding: 0 16px; background: #141a25; border-bottom: 1px solid #263044; }
  .bar i { width: 12px; height: 12px; border-radius: 50%; display: inline-block; }
  .bar b { margin-left: 12px; color: #7f8aa0; font-weight: 500; font-size: 14px; font-family: Consolas, monospace; }
  pre { padding: 18px 22px; font-family: "Cascadia Mono", Consolas, monospace; font-size: 17px; line-height: 1.55; white-space: pre-wrap; }
  .cmd { color: #e9edf5; overflow: hidden; white-space: nowrap; width: 0; animation: type 1.4s steps(60, end) .2s forwards; }
  .cmd::before { content: "$ "; color: #5ee08b; }
  @keyframes type { to { width: 100%; } }
  .ln { opacity: 0; animation: in .25s ease forwards; }
  .n { color: #7cc4ff; } .l { color: #c79bff; } .v { color: #ffc53d; } .c { color: #6b7590; }
  .note { margin-top: 14px; font-size: 18px; color: #9aa5ba; }
  </style></head><body><div class="wrap">
    <div class="kicker fade">Метрики для Prometheus</div>
    <h1 class="fade" style="animation-delay:.15s">/metrics: бизнес-показатели в формате Prometheus</h1>
    <div class="term fade" style="animation-delay:.3s">
      <div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i><i style="background:#28c840"></i><b>bash</b></div>
      <pre><div class="cmd">${esc(command)}</div>${body}</pre>
    </div>
    <div class="note fade" style="animation-delay:1s">${esc(note)}</div>
  </div></body></html>`
}

export function architectureSlide(): string {
  const box = (x: number, y: number, w: number, h: number, title: string, sub: string, cls = '', delay = 0) =>
    `<div class="box ${cls}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px;animation-delay:${delay}s"><b>${esc(title)}</b><span>${esc(sub)}</span></div>`
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Под капотом</title><style>${BASE_CSS}
  .wrap { padding: 40px 64px 160px; }
  h1 { font-size: 40px; }
  .stage { position: relative; margin-top: 22px; height: 560px; }
  .box { position: absolute; border-radius: 14px; padding: 12px 14px; background: #151c29; border: 1px solid #2c3850;
    display: flex; flex-direction: column; gap: 4px; opacity: 0; transform: translateY(8px); animation: in .6s ease forwards; }
  .box b { font-size: 18px; font-weight: 650; }
  .box span { font-size: 14px; color: #9aa5ba; line-height: 1.35; }
  .gw { background: #1d2536; border-color: #3b4a68; }
  .bus { background: #2a2110; border-color: #ffc53d; }
  .bus b { color: #ffc53d; }
  .store { background: #13202a; border-color: #2f5168; }
  .obs { background: #1a1630; border-color: #4b3f86; }
  .k8s { background: #10241c; border-color: #2f6a4e; }
  .label { position: absolute; font-size: 13px; letter-spacing: .12em; text-transform: uppercase; color: #6f7a91; opacity: 0; animation: in .6s ease forwards; }
  svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  svg path { stroke: #4a5874; stroke-width: 2; fill: none; stroke-dasharray: 6 6; animation: flow 1.2s linear infinite; opacity: 0; }
  svg path.on { animation: flow 1.2s linear infinite, show .6s ease forwards; }
  svg path.hot { stroke: #ffc53d; }
  @keyframes flow { to { stroke-dashoffset: -24; } }
  @keyframes show { to { opacity: 1; } }
  </style></head><body><div class="wrap">
    <div class="kicker fade">Что под капотом</div>
    <h1 class="fade" style="animation-delay:.1s">Микросервисы, шина событий и честная проверка надёжности</h1>
    <div class="stage">
      <svg viewBox="0 0 1312 560" preserveAspectRatio="none">
        <path class="on" style="animation-delay:0s,1.2s" d="M150 70 L150 140"/>
        <path class="on" style="animation-delay:0s,1.3s" d="M260 175 L330 175"/>
        <path class="on hot" style="animation-delay:0s,1.5s" d="M510 210 L510 300"/>
        <path class="on hot" style="animation-delay:0s,1.6s" d="M700 210 L700 300"/>
        <path class="on hot" style="animation-delay:0s,1.7s" d="M890 300 L890 210"/>
        <path class="on hot" style="animation-delay:0s,1.8s" d="M1080 300 L1080 210"/>
        <path class="on" style="animation-delay:0s,1.9s" d="M600 375 L600 455"/>
        <path class="on" style="animation-delay:0s,2.0s" d="M1170 140 L1170 70"/>
      </svg>
      <div class="label" style="left:0;top:0;animation-delay:.2s">Вход</div>
      ${box(40, 10, 220, 60, 'Браузер', 'React-панель, SSE', '', 0.3)}
      ${box(40, 140, 220, 70, 'Traefik', 'шлюз, ForwardAuth, CSRF', 'gw', 0.4)}
      ${box(40, 240, 220, 70, 'auth', 'сессии, роли, OIDC и SAML', '', 0.5)}
      ${box(40, 340, 220, 60, 'Authentik', 'корпоративный SSO', '', 0.6)}
      <div class="label" style="left:330;top:112px;left:330px;animation-delay:.6s">Сервисы</div>
      ${box(330, 140, 170, 70, 'ingest', 'снимки и поток', '', 0.7)}
      ${box(520, 140, 170, 70, 'api ×2', 'рейтинг, настройки', '', 0.8)}
      ${box(710, 140, 170, 70, 'scoring ×2', 'score, причины', '', 0.9)}
      ${box(900, 140, 170, 70, 'notifier ×2', 'SSE в браузер', '', 1.0)}
      ${box(1090, 140, 190, 70, 'alerts ×2', 'почта, Telegram', '', 1.1)}
      ${box(330, 300, 950, 75, 'NATS JetStream + transactional outbox', 'событие пишется в одной транзакции с данными: при упавшей шине не теряется и не дублируется', 'bus', 1.3)}
      ${box(330, 455, 540, 70, 'PostgreSQL 16 · Redis 7', 'данные, оценки, сессии и лимиты', 'store', 1.5)}
      ${box(890, 455, 390, 70, 'Prometheus · Grafana', '/metrics, дашборды, алерты', 'obs', 1.6)}
      ${box(1090, 10, 190, 60, 'Mailpit / SMTP', 'письма клиентам', '', 1.2)}
      ${box(40, 455, 270, 70, 'Kubernetes', 'Helm-чарт, kind в CI на каждый PR', 'k8s', 1.8)}
      ${box(520, 10, 520, 60, 'Хаос-проверка: chaos.sh роняет NATS, api, scoring', 'данные доходят, панель отвечает', 'k8s', 2.0)}
    </div>
  </div></body></html>`
}

export function endSlide(): string {
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Спасибо</title><style>${BASE_CSS}
  .center { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; padding-bottom: 140px; }
  .logo { font-size: 72px; font-weight: 800; letter-spacing: -.03em; } .logo span { color: #ffc53d; }
  .sub { margin-top: 18px; font-size: 26px; color: #b9c2d3; }
  </style></head><body><div class="center">
    <div class="logo fade">Churn<span>Radar</span></div>
    <div class="sub fade" style="animation-delay:.3s">Спасибо за внимание</div>
  </div></body></html>`
}
