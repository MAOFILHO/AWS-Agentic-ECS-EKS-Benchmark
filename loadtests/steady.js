// Steady: constant RPS for the whole run. Baseline latency and error rate.
import { durationS, peakRps, pool, triage } from "./common.js";

export const options = {
  scenarios: {
    steady: { executor: "constant-arrival-rate", rate: peakRps, duration: `${durationS}s`, ...pool },
  },
};
export default triage;
