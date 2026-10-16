// Step: RPS rises in four steps (25, 50, 75 and 100 percent of the peak). Latency per load band.
import { peakRps, pool, quarterS, triage } from "./common.js";

const rates = [0.25, 0.5, 0.75, 1].map((share) => Math.max(1, Math.round(peakRps * share)));
const stages = rates.flatMap((target) => [
  { target, duration: "1s" }, // jump to the step
  { target, duration: `${quarterS}s` }, // hold it
]);

export const options = {
  scenarios: {
    step: { executor: "ramping-arrival-rate", startRate: rates[0], stages, ...pool },
  },
};
export default triage;
