// Shared by the four profiles. Environment: TARGET_URL (base URL), PEAK_RPS (default 50),
// DURATION_S (default 60).
import http from "k6/http";
import { check } from "k6";

const target = (__ENV.TARGET_URL || "http://localhost:8080").replace(/\/+$/, "");
export const peakRps = Number(__ENV.PEAK_RPS || 50);
export const durationS = Number(__ENV.DURATION_S || 60);
export const quarterS = Math.max(1, Math.floor(durationS / 4));

// Arrival-rate executors set the request rate and add VUs as needed, up to maxVUs.
export const pool = {
  timeUnit: "1s",
  preAllocatedVUs: Math.max(10, Math.ceil(peakRps)),
  maxVUs: Math.max(50, Math.ceil(peakRps * 5)),
};

const params = { headers: { "Content-Type": "application/json" } };
const body = JSON.stringify({ text: "My invoice shows a charge I do not recognise. Please check it." });

export function triage() {
  const res = http.post(`${target}/triage`, body, params);
  check(res, { "status is 200": (r) => r.status === 200 });
}
