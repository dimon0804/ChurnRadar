import http from 'k6/http';
import { fail } from 'k6';

export const BASE_URL = (__ENV.BASE_URL || 'http://traefik:8080').replace(/\/$/, '');
const USERNAME = __ENV.USERNAME || 'admin';
const PASSWORD = __ENV.PASSWORD || 'admin';

// Логинимся один раз в setup() и раздаём cookie всем VU: так не упираемся
// в rate-limit логина и меряем именно чтение данных.
export function login() {
  const res = http.post(
    `${BASE_URL}/api/v1/auth/login`,
    JSON.stringify({ username: USERNAME, password: PASSWORD }),
    { headers: { 'Content-Type': 'application/json', Origin: BASE_URL }, tags: { name: 'login' } },
  );
  const cookie = res.cookies.cr_session && res.cookies.cr_session[0];
  if (res.status !== 200 || !cookie) {
    fail(`login failed: status=${res.status} body=${String(res.body).slice(0, 200)}`);
  }
  return cookie.value;
}

export function authHeaders(session, extra = {}) {
  return { Cookie: `cr_session=${session}`, Origin: BASE_URL, ...extra };
}

export function pick(arr) {
  return arr[Math.floor(Math.random() * arr.length)];
}
