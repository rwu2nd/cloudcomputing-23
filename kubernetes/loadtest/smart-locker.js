// k6 load test: a rush of users browsing lockers, signing in and checking
// their parcels. Signing in is deliberately part of it: password hashing
// (PBKDF2) is CPU-heavy, which is what makes the backend autoscaler react.
//
// In the cluster:  make k8s-loadtest          (see kubernetes/loadtest/)
// Against Compose: docker run --rm -i -e BASE_URL=http://host.docker.internal:3000 \
//                    grafana/k6:1.8.2 run - < kubernetes/loadtest/smart-locker.js
//
// Settings (environment variables): BASE_URL, PEAK_USERS (default 40),
// HOLD (how long to stay at the peak, default 2m).

import http from "k6/http";
import { check, sleep } from "k6";

const BASE = (__ENV.BASE_URL || "http://localhost:3000").replace(/\/$/, "");
const PEAK = parseInt(__ENV.PEAK_USERS || "40", 10);
const HOLD = __ENV.HOLD || "2m";
const PASSWORD = "LoadTest-12345";
const JSON_HEADERS = { "Content-Type": "application/json" };

export const options = {
  scenarios: {
    rush: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: Math.max(1, Math.ceil(PEAK / 4)) },
        { duration: "45s", target: PEAK },
        { duration: HOLD, target: PEAK },
        { duration: "30s", target: 0 },
      ],
      gracefulRampDown: "10s",
    },
  },
  thresholds: {
    // The run "fails" if more than 2% of requests fail.
    http_req_failed: ["rate<0.02"],
  },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

// Register a pool of accounts once, before the virtual users start.
export function setup() {
  const prefix = "load" + Date.now().toString(36);
  const users = [];
  for (let i = 0; i < 20; i++) {
    const username = `${prefix}-${i}`;
    const res = http.post(`${BASE}/api/luggage/auth/register`,
      JSON.stringify({ username, password: PASSWORD }), { headers: JSON_HEADERS });
    if (res.status === 201) users.push(username);
  }
  if (users.length === 0) throw new Error(`could not register test users at ${BASE}`);
  return { users };
}

export default function (data) {
  const username = data.users[(__VU + __ITER) % data.users.length];

  const stations = http.get(`${BASE}/api/stations`, { tags: { name: "GET /api/stations" } });
  check(stations, { "stations: 200": (r) => r.status === 200 });

  const login = http.post(`${BASE}/api/luggage/auth/login`,
    JSON.stringify({ username, password: PASSWORD }),
    { headers: JSON_HEADERS, tags: { name: "POST /api/luggage/auth/login" } });
  check(login, { "login: 200": (r) => r.status === 200 });

  if (login.status === 200) {
    const auth = { headers: { Authorization: `Bearer ${login.json("access_token")}` } };
    const parcels = http.get(`${BASE}/api/parcels`, Object.assign({ tags: { name: "GET /api/parcels" } }, auth));
    check(parcels, { "parcels: 200": (r) => r.status === 200 });
    const slots = http.get(`${BASE}/api/luggage/slots`, Object.assign({ tags: { name: "GET /api/luggage/slots" } }, auth));
    check(slots, { "slots: 200": (r) => r.status === 200 });
  }

  sleep(1);
}
