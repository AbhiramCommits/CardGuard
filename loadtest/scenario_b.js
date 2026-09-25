import { post } from 'k6/http';
import { SharedArray } from 'k6/data';
import { Counter } from 'k6/metrics';
import { buildRequest, summaryFor } from './common.js';

const config = new SharedArray('config', function () {
  return JSON.parse(open('./results/loadtest-config.json', 'r')).cards;
});

const REPLAY_RATE = 0.2;
const replays = new Counter('idempotent_replays');

export const options = {
  scenarios: {
    retry_storm: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 60,
      maxVUs: 300,
      stages: [
        { duration: '10s', target: 10 },
        { duration: '10s', target: 20 },
        { duration: '10s', target: 30 },
        { duration: '10s', target: 40 },
        { duration: '60s', target: 50 },
      ],
    },
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(99)<500'],
  },
};

export default async function () {
  const { payload } = buildRequest(config, REPLAY_RATE);
  const response = await post(
    'http://localhost:8000/v1/authorizations',
    JSON.stringify(payload),
    { headers: { 'Content-Type': 'application/json' } },
  );
  if (response.headers['Idempotent-Replay'] === 'true') {
    replays.add(1);
  }
  if (response.status !== 200) {
    console.error(`unexpected status ${response.status}: ${response.body}`);
  }
}

export function handleSummary(data) {
  return summaryFor(data, 'scenario_b');
}
