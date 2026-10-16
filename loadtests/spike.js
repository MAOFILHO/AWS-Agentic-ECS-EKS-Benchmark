// Spike: a quiet quarter at 10 percent of the peak, then a jump to the peak within one second.
import { durationS, peakRps, pool, quarterS, triage } from "./common.js";

const base = Math.max(1, Math.round(peakRps * 0.1));
const stages = [
  { target: base, duration: `${quarterS}s` },
  { target: peakRps, duration: "1s" },
  { target: peakRps, duration: `${Math.max(1, durationS - quarterS)}s` },
];

export const options = {
  scenarios: {
    spike: { executor: "ramping-arrival-rate", startRate: base, stages, ...pool },
  },
};
export default triage;
