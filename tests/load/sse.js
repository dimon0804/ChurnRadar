// 500 одновременных SSE-клиентов на GET /api/v1/stream.
//
// В стандартном образе grafana/k6 нет SSE-клиента (он есть только в сборке с
// расширением xk6-sse). Поэтому каждый VU делает обычный GET с таймаутом HOLD:
// k6 держит соединение открытым, пока сервер шлёт поток, и обрывает его по
// таймауту. Удержанное до конца соединение (ошибка 1050 — request timeout,
// а не быстрый 401/502) считаем успехом в метрике sse_held.
// Число живых подключений во время теста видно в Grafana («Платформа»,
// notifier_connected_clients).
import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend } from 'k6/metrics';
import { BASE_URL, login, authHeaders } from './lib.js';

const CLIENTS = Number(__ENV.CLIENTS || 500);
const HOLD_SECONDS = Number(__ENV.HOLD_SECONDS || 60);
const DURATION = __ENV.DURATION || '3m';

const held = new Rate('sse_held');
const heldFor = new Trend('sse_held_seconds');

export const options = {
  scenarios: {
    sse: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { target: CLIENTS, duration: '30s' },
        { target: CLIENTS, duration: DURATION },
        { target: 0, duration: '5s' },
      ],
      gracefulRampDown: `${HOLD_SECONDS + 5}s`,
      gracefulStop: `${HOLD_SECONDS + 5}s`,
    },
  },
  thresholds: {
    sse_held: ['rate>0.99'],
  },
};

export function setup() {
  return { session: login() };
}

export default function (data) {
  const started = Date.now();
  const res = http.get(`${BASE_URL}/api/v1/stream`, {
    headers: authHeaders(data.session, { Accept: 'text/event-stream', 'Cache-Control': 'no-cache' }),
    timeout: `${HOLD_SECONDS}s`,
    tags: { name: 'stream' },
    responseCallback: http.expectedStatuses(200),
  });
  const seconds = (Date.now() - started) / 1000;
  const ok = res.error_code === 1050 && seconds >= HOLD_SECONDS * 0.95;
  held.add(ok);
  if (ok) heldFor.add(seconds);
  check(res, { 'stream held open': () => ok });
}
