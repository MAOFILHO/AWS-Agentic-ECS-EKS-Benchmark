// Scale from min: no traffic for the first quarter, so the cell sits at its minimum replicas,
// then the full peak at once. Cold start and first-replica time.
import { durationS, peakRps, pool, quarterS, triage } from "./common.js";

const stages = [
  { target: 0, duration: `${quarterS}s` },
  { target: peakRps, duration: "1s" },
  { target: peakRps, duration: `${Math.max(1, durationS - quarterS)}s` },
];

export const options = {
  scenarios: {
    scale_from_min: { executor: "ramping-arrival-rate", startRate: 0, stages, ...pool },
  },
};
export default triage;
