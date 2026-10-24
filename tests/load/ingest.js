// Поток изменений источника: небольшие пакеты upsert в POST /api/v1/source/events.
// Берём реальные id/email клиентов из рейтинга и меняем им баланс и пополнения,
// так каждое событие запускает пересчёт и уходит в SSE.
import http from 'k6/http';
import { check, fail } from 'k6';
import { BASE_URL, login, authHeaders, pick } from './lib.js';

const RATE = Number(__ENV.RATE || 20);
const DURATION = __ENV.DURATION || '2m';
const BATCH = Number(__ENV.BATCH || 5);

export const options = {
  scenarios: {
    ingest: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: 20,
      maxVUs: 200,
    },
  },
  thresholds: {
    'http_req_duration{name:source_events}': ['p(95)<500'],
    'http_req_failed{name:source_events}': ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  const session = login();
  const accounts = [];
  for (let page = 1; page <= 5; page++) {
    const res = http.get(`${BASE_URL}/api/v1/customers?sort=id&order=asc&page_size=200&page=${page}`, {
      headers: authHeaders(session),
      tags: { name: 'setup' },
    });
    if (res.status !== 200) fail(`customers: ${res.status}`);
    const items = res.json('items') || [];
    for (const c of items) {
      accounts.push({
        id: c.id,
        email: c.email,
        bonusBalance: c.bonus_balance,
        bonusState: c.bonus_state || 'locked',
        totalReplenished: c.total_replenished,
      });
    }
    if (items.length < 200) break;
  }
  if (accounts.length === 0) fail('no customers to update');
  return { session, accounts };
}

function randInt(max) {
  return Math.floor(Math.random() * max);
}

export default function (data) {
  const batch = [];
  for (let i = 0; i < BATCH; i++) {
    const a = pick(data.accounts);
    const balance = randInt(200000);
    batch.push({
      id: a.id,
      email: a.email,
      balance,
      withdrawBalance: randInt(balance + 1),
      totalReplenished: a.totalReplenished + randInt(5000),
      bonusBalance: a.bonusBalance,
      bonusState: a.bonusState,
    });
  }
  const res = http.post(`${BASE_URL}/api/v1/source/events`, JSON.stringify({ accounts: batch }), {
    headers: authHeaders(data.session, { 'Content-Type': 'application/json' }),
    tags: { name: 'source_events' },
  });
  check(res, { 'status 202': (r) => r.status === 202 });
}
