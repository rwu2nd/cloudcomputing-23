// k6 stress test for the Smart Locker system. Runs INSIDE the cluster, so its
// traffic goes through the frontend Service and is spread over all backend pods.
//
// Started from the admin dashboard (Testing System -> Stress Test), which
// creates throwaway accounts and the "Stress Test Bay" test lockers and passes
// them in. Scenarios (any combination, run at the same time):
//
//   browse   look at lockers and sign in: CPU-heavy, drives the autoscaler
//   parcel   full parcel journey on the test lockers: assign, book, wait for
//            allocation, arrive, unlock the door over gRPC
//   luggage  store luggage in a test XL locker, pay, unlock, collect
//   churn    book and immediately cancel, to stress the allocation queue
//
// Chaos events during the run (optional), spaced out so they do not overlap:
//   faults      40% controller faults from 15% to 40% of the run
//   controller  door controller stopped from 45% of the run for 10-30 s
//   failover    database primary killed at 70% of the run
// Like a real user, journeys retry a failed door unlock instead of giving up.
//
// Every finished journey, chaos event and error is printed with console.log;
// the dashboard shows those lines as its live console.
//
// `make k8s-loadtest` (no SCENARIOS set) runs the plain browse test:
// PEAK_USERS users for HOLD at the peak, registering its own accounts.

import http from "k6/http";
import exec from "k6/execution";
import { check, sleep } from "k6";

const BASE = (__ENV.BASE_URL || "http://localhost:3000").replace(/\/$/, "");
const PRESET = Boolean(__ENV.SCENARIOS);
const SCENARIOS = (__ENV.SCENARIOS || "browse").split(",").filter(Boolean);
const CHAOS = (__ENV.CHAOS || "").split(",").filter(Boolean);
const DURATION = parseInt(__ENV.DURATION_S || "0", 10);
const VUS = JSON.parse(__ENV.VUS_JSON || "{}");
const USERS = JSON.parse(__ENV.USERS_JSON || "{}");
const LABELS = JSON.parse(__ENV.LABELS_JSON || "{}");
const STATION = parseInt(__ENV.STATION_ID || "0", 10);
const ADMIN = __ENV.ADMIN_TOKEN || "";
const PASSWORD = __ENV.PASSWORD || "LoadTest-12345";
const JSON_HEADERS = { "Content-Type": "application/json" };

// Conflicts are an expected part of contention (locker just taken, etc.).
http.setResponseCallback(http.expectedStatuses({ min: 200, max: 299 }, 409));

function buildScenarios() {
  const s = {};
  if (!PRESET) {
    const peak = parseInt(__ENV.PEAK_USERS || "40", 10);
    s.browse = {
      executor: "ramping-vus", exec: "browse", startVUs: 0, gracefulRampDown: "10s",
      stages: [
        { duration: "30s", target: Math.max(1, Math.ceil(peak / 4)) },
        { duration: "45s", target: peak },
        { duration: __ENV.HOLD || "2m", target: peak },
        { duration: "30s", target: 0 },
      ],
    };
    return s;
  }
  const d = DURATION;
  if (SCENARIOS.includes("browse")) {
    s.browse = {
      executor: "ramping-vus", exec: "browse", startVUs: 0, gracefulRampDown: "10s",
      stages: [
        { duration: `${Math.round(d * 0.25)}s`, target: VUS.browse },
        { duration: `${Math.round(d * 0.55)}s`, target: VUS.browse },
        { duration: `${Math.round(d * 0.2)}s`, target: 0 },
      ],
    };
  }
  for (const name of ["parcel", "luggage", "churn"]) {
    if (SCENARIOS.includes(name))
      s[name] = { executor: "constant-vus", exec: name, vus: VUS[name], duration: `${d}s`, gracefulStop: "30s" };
  }
  const once = (fn, start) => ({
    executor: "per-vu-iterations", exec: fn, vus: 1, iterations: 1,
    startTime: `${Math.round(start)}s`, maxDuration: `${d + 30}s`,
  });
  if (CHAOS.includes("faults")) s.chaos_faults = once("chaosFaults", d * 0.15);
  if (CHAOS.includes("controller")) s.chaos_controller = once("chaosController", d * 0.45);
  if (CHAOS.includes("failover")) s.chaos_failover = once("chaosFailover", d * 0.7);
  return s;
}

export const options = {
  scenarios: buildScenarios(),
  thresholds: PRESET ? {} : { http_req_failed: ["rate<0.02"] },
  summaryTrendStats: ["avg", "p(50)", "p(95)", "p(99)", "max"],
};

// ------------------------------------------------------------ helpers

const auth = (token) => ({ headers: { ...JSON_HEADERS, Authorization: `Bearer ${token}` } });
const post = (path, body, token, name) =>
  http.post(`${BASE}${path}`, body === null ? null : JSON.stringify(body), { ...auth(token), tags: { name: name || path } });
const get = (path, token, name) => http.get(`${BASE}${path}`, { ...auth(token), tags: { name: name || path } });
const del = (path, token, name) => http.del(`${BASE}${path}`, null, { ...auth(token), tags: { name: name || path } });
const short = (u) => u.username.replace(/^stress-[0-9a-f]+-/, "");
const label = (id) => LABELS[id] || `#${id}`;
const detail = (r) => {
  try { return r.json("detail") || r.status; } catch (e) { return r.status; }
};
function pick(scenario) {
  const list = USERS[scenario] || [];
  return list[exec.vu.idInTest % list.length];
}
function log(scenario, text) {
  console.log(`[${scenario}] ${text}`);
}
// Tap "Unlock" again until it works (controller outage, failover), for up to
// 45 s. Returns the last response.
function unlockWithRetry(path, token, name, scenario, who, what) {
  const started = Date.now();
  let r = post(path, null, token, name);
  let tries = 1;
  while (r.status !== 200 && r.status >= 500 && Date.now() - started < 45000) {
    if (tries === 1) log(scenario, `${who} unlock of ${what} failed (${r.status} ${detail(r)}), retrying`);
    sleep(2);
    r = post(path, null, token, name);
    tries++;
  }
  if (r.status === 200 && tries > 1)
    log(scenario, `${who} unlock of ${what} worked after ${tries} tries (${((Date.now() - started) / 1000).toFixed(0)} s)`);
  return r;
}

export function setup() {
  if (PRESET) {
    const parts = SCENARIOS.map((s) => `${s}${VUS[s] ? ` x${VUS[s]}` : ""}`).join(", ");
    console.log(`Stress test starting: ${parts}; ${DURATION}s` + (CHAOS.length ? `; chaos: ${CHAOS.join(", ")}` : "") +
      `; test lockers at station ${STATION}`);
    return { browseUsers: (USERS.browse || []).map((u) => u.username) };
  }
  // Plain `make k8s-loadtest`: register our own accounts.
  const prefix = "load" + Date.now().toString(36);
  const browseUsers = [];
  for (let i = 0; i < 20; i++) {
    const username = `${prefix}-${i}`;
    const res = http.post(`${BASE}/api/luggage/auth/register`,
      JSON.stringify({ username, password: PASSWORD }), { headers: JSON_HEADERS });
    if (res.status === 201) browseUsers.push(username);
  }
  if (browseUsers.length === 0) throw new Error(`could not register test users at ${BASE}`);
  console.log(`Load test starting with ${browseUsers.length} accounts`);
  return { browseUsers };
}

// ------------------------------------------------------------ scenarios

export function browse(data) {
  const username = data.browseUsers[(exec.vu.idInTest + exec.vu.iterationInScenario) % data.browseUsers.length];
  const stations = http.get(`${BASE}/api/stations`, { tags: { name: "GET /api/stations" } });
  check(stations, { "stations: 200": (r) => r.status === 200 });
  const login = http.post(`${BASE}/api/luggage/auth/login`, JSON.stringify({ username, password: PASSWORD }),
    { headers: JSON_HEADERS, tags: { name: "POST /api/luggage/auth/login" } });
  check(login, { "login: 200": (r) => r.status === 200 });
  if (login.status === 200) {
    const token = login.json("access_token");
    check(get("/api/parcels", token, "GET /api/parcels"), { "parcels: 200": (r) => r.status === 200 });
    check(get("/api/luggage/slots", token, "GET /api/luggage/slots"), { "slots: 200": (r) => r.status === 200 });
    if (exec.vu.iterationInScenario % 25 === 0)
      log("browse", `${username.replace(/^stress-[0-9a-f]+-/, "")} signed in and browsed (${login.timings.duration.toFixed(0)} ms to sign in)`);
  } else {
    log("browse", `sign-in failed: ${login.status} ${detail(login)}`);
  }
  sleep(1);
}

export function parcel() {
  const u = pick("parcel");
  const size = ["S", "M", "M", "L"][Math.floor(Math.random() * 4)];
  const assigned = post("/api/admin/parcels", { user_id: u.id, size, description: "stress test" }, ADMIN, "POST /api/admin/parcels");
  if (assigned.status !== 201) { log("parcel", `could not assign a parcel: ${assigned.status} ${detail(assigned)}`); sleep(2); return; }
  const booked = post("/api/reservations", { station_id: STATION, parcel_id: assigned.json("id") }, u.token, "POST /api/reservations");
  if (booked.status !== 202) { log("parcel", `${short(u)} booking failed: ${booked.status} ${detail(booked)}`); sleep(2); return; }
  const id = booked.json("id");
  const waitStart = Date.now();
  let current = null;
  while (Date.now() - waitStart < 15000) {
    sleep(0.4);
    const r = get(`/api/reservations/${id}`, u.token, "GET /api/reservations/{id}");
    if (r.status === 200 && r.json("status") === "ALLOCATED") { current = r.json(); break; }
  }
  if (!current) {
    del(`/api/reservations/${id}`, u.token, "DELETE /api/reservations/{id}");
    log("parcel", `${short(u)} waited 15 s for a free ${size} locker, gave up and cancelled`);
    return;
  }
  const waited = ((Date.now() - waitStart) / 1000).toFixed(1);
  const arrived = post(`/api/reservations/${id}/arrive`, null, u.token, "POST /api/reservations/{id}/arrive");
  if (arrived.status !== 200) { log("parcel", `${short(u)} arrive failed: ${arrived.status} ${detail(arrived)}`); return; }
  sleep(1 + Math.random() * 2);
  const unlocked = unlockWithRetry(`/api/reservations/${id}/unlock`, u.token, "POST /api/reservations/{id}/unlock",
    "parcel", short(u), label(current.compartment_id));
  if (unlocked.status === 200) {
    const door = unlocked.json("door") || {};
    log("parcel", `${short(u)} ${size} parcel -> ${label(current.compartment_id)} after ${waited} s -> arrived -> ` +
      `door opened by ${door.controller} (${unlocked.timings.duration.toFixed(0)} ms) -> collected`);
  } else {
    // Give the locker back rather than leaving it occupied for the rest of the run.
    del(`/api/reservations/${id}`, u.token, "DELETE /api/reservations/{id}");
    log("parcel", `${short(u)} unlock of ${label(current.compartment_id)} FAILED for 45 s: ${unlocked.status} ${detail(unlocked)}; cancelled to free the locker`);
  }
}

export function luggage() {
  const u = pick("luggage");
  const slots = get("/api/luggage/slots", u.token, "GET /api/luggage/slots");
  const free = slots.status === 200 ? slots.json() : [];
  if (!free.length) { log("luggage", `${short(u)} found every test XL locker busy, waiting`); sleep(2); return; }
  const slot = free[Math.floor(Math.random() * free.length)];
  const stored = post("/api/luggage/sessions", { compartment_id: slot.compartment_id }, u.token, "POST /api/luggage/sessions");
  if (stored.status === 409) { log("luggage", `${short(u)} lost ${slot.label} to someone faster, trying again`); return; }
  if (stored.status !== 201) { log("luggage", `${short(u)} store FAILED: ${stored.status} ${detail(stored)}`); sleep(1); return; }
  const session = stored.json("session_id");
  sleep(1 + Math.random() * 2);
  const checkout = post(`/api/luggage/sessions/${session}/checkout`, null, u.token, "POST /api/luggage/sessions/{id}/checkout");
  const paid = checkout.status === 200
    ? post(`/api/luggage/payments/${checkout.json("payment_reference")}/demo-pay`, null, u.token, "POST /api/luggage/payments/{ref}/demo-pay")
    : checkout;
  const unlocked = paid.status === 200
    ? unlockWithRetry(`/api/luggage/sessions/${session}/unlock`, u.token, "POST /api/luggage/sessions/{id}/unlock", "luggage", short(u), slot.label)
    : paid;
  const done = unlocked.status === 200 ? post(`/api/luggage/sessions/${session}/complete`, null, u.token, "POST /api/luggage/sessions/{id}/complete") : unlocked;
  if (done.status === 200) {
    log("luggage", `${short(u)} stored luggage in ${slot.label} -> paid ${checkout.json("amount")} -> door opened by ` +
      `${(unlocked.json("door") || {}).controller} -> collected`);
  } else {
    log("luggage", `${short(u)} luggage in ${slot.label} FAILED: ${done.status} ${detail(done)}`);
  }
}

export function churn() {
  const u = pick("churn");
  const assigned = post("/api/admin/parcels", { user_id: u.id, size: "S", description: "stress churn" }, ADMIN, "POST /api/admin/parcels");
  if (assigned.status !== 201) { sleep(1); return; }
  const booked = post("/api/reservations", { station_id: STATION, parcel_id: assigned.json("id") }, u.token, "POST /api/reservations");
  if (booked.status !== 202) { log("churn", `${short(u)} booking failed: ${booked.status} ${detail(booked)}`); sleep(1); return; }
  sleep(0.3 + Math.random() * 0.5);
  const cancelled = del(`/api/reservations/${booked.json("id")}`, u.token, "DELETE /api/reservations/{id}");
  if (exec.vu.iterationInScenario % 5 === 0 || cancelled.status !== 200)
    log("churn", `${short(u)} booked and cancelled (${cancelled.status === 200 ? "ok" : `${cancelled.status} ${detail(cancelled)}`})`);
  sleep(0.5);
}

// ------------------------------------------------------------ chaos

// Retried, because a chaos step can land while another one (a failover) is
// still in progress, and "start the controller again" must not be lost.
function chaosCall(path, body, what) {
  let r = post(`/api/admin/demo/fault/${path}`, body, ADMIN, `chaos ${path}`);
  for (let i = 0; r.status !== 200 && i < 15; i++) {
    if (i === 0) console.log(`[chaos] ${what}: got ${r.status} ${detail(r)}, retrying`);
    sleep(2);
    r = post(`/api/admin/demo/fault/${path}`, body, ADMIN, `chaos ${path}`);
  }
  console.log(r.status === 200 ? `[chaos] ${what}` : `[chaos] ${what} FAILED: ${r.status} ${detail(r)}`);
  return r;
}

export function chaosFaults() {
  chaosCall("faults", { failure_rate: 0.4 }, "controller now fails 40% of calls on purpose (gRPC should retry them)");
  sleep(DURATION * 0.25);
  chaosCall("faults", { failure_rate: 0 }, "controller faults cleared");
}

export function chaosController() {
  chaosCall("controller", { replicas: 0 }, "door controller stopped: unlocks should fail with 503, bookings keep working");
  sleep(Math.max(10, Math.min(30, DURATION * 0.15)));
  chaosCall("controller", { replicas: 2 }, "door controller started again (2 pods)");
}

export function chaosFailover() {
  const r = chaosCall("failover", null, "killing the database primary");
  if (r.status === 200) console.log(`[chaos] deleted ${r.json("deleted")}; a replica should be promoted within seconds`);
}

export function teardown() {
  if (!PRESET) return;
  if (CHAOS.includes("faults")) post("/api/admin/demo/fault/faults", { failure_rate: 0 }, ADMIN, "chaos restore");
  if (CHAOS.includes("controller")) post("/api/admin/demo/fault/controller", { replicas: 2 }, ADMIN, "chaos restore");
  const r = post("/api/admin/demo/stress/reset", null, ADMIN, "stress reset");
  console.log(r.status === 200 ? `Run finished: released ${r.json("released")} test lockers` : `Run finished; reset failed: ${r.status}`);
}
