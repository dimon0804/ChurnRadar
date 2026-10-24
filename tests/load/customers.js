// Рейтинг клиентов под нагрузкой: разгон до 200 RPS на GET /api/v1/customers
// со случайной сортировкой, направлением и страницей.
import http from 'k6/http';
import { check } from 'k6';
import { BASE_URL, login, authHeaders, pick } from './lib.js';

const TARGET_RPS = Number(__ENV.TARGET_RPS || 200);
const HOLD = __ENV.HOLD || '2m';
const MAX_PAGE = Number(__ENV.MAX_PAGE || 20);

const SORTS = ['id', 'email', 'balance', 'total_replenished', 'risk_score', 'expected_loss'];
const ORDERS = ['asc', 'desc'];
const LEVELS = ['', '', '', 'high', 'high,medium', 'low'];

export const options = {
  scenarios: {
    customers: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 100,
      maxVUs: 600,
      stages: [
        { target: TARGET_RPS, duration: '30s' },
        { target: TARGET_RPS, duration: HOLD },
        { target: 0, duration: '10s' },
      ],
    },
  },
  thresholds: {
    'http_req_duration{name:customers}': ['p(95)<300'],
    'http_req_failed{name:customers}': ['rate<0.01'],
    checks: ['rate>0.99'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export function setup() {
  return { session: login() };
}

export default function (data) {
  const params = [
    `sort=${pick(SORTS)}`,
    `order=${pick(ORDERS)}`,
    `page=${1 + Math.floor(Math.random() * MAX_PAGE)}`,
    `page_size=${pick([25, 50, 100])}`,
  ];
  const level = pick(LEVELS);
  if (level) params.push(`level=${level}`);

  const res = http.get(`${BASE_URL}/api/v1/customers?${params.join('&')}`, {
    headers: authHeaders(data.session),
    tags: { name: 'customers' },
  });
  check(res, {
    'status 200': (r) => r.status === 200,
    'has items': (r) => r.status === 200 && Array.isArray(r.json('items')),
  });
}
