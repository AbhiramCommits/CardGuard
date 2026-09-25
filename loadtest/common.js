export const PROFILES = [
  { name: 'approve', weight: 0.6, mcc: '5411', amount: [2000, 9000] },
  { name: 'decline', weight: 0.25, mcc: '5541', amount: [1000, 5000] },
  { name: 'review', weight: 0.15, mcc: '5411', amount: [9500, 9500] },
];

let keyCounter = 0;

export const replayPool = {
  keys: [],
  entries: {},
};

function weightedProfile() {
  const r = Math.random();
  let acc = 0;
  for (const profile of PROFILES) {
    acc += profile.weight;
    if (r <= acc) return profile;
  }
  return PROFILES[0];
}

export function cardByName(config, name) {
  return config.find((card) => card.name === name);
}

export function buildRequest(config, replayRate) {
  if (replayRate > 0 && replayPool.keys.length > 0 && Math.random() < replayRate) {
    const key = replayPool.keys[Math.floor(Math.random() * replayPool.keys.length)];
    return replayPool.entries[key];
  }

  const profile = weightedProfile();
  const card = cardByName(config, profile.name);
  const [lo, hi] = profile.amount;
  const amount = lo + Math.floor(Math.random() * (hi - lo + 1));
  const payload = {
    idempotency_key: `${__ENV.RUN_ID || 'local'}-${Date.now()}-${keyCounter++}-${Math.floor(Math.random() * 1e9)}`,
    card_token: card.token,
    merchant_name: profile.name === 'decline' ? 'GAS STATION 5541' : 'LOADTEST MERCHANT',
    mcc: profile.mcc,
    amount_cents: amount,
    timestamp: new Date().toISOString(),
  };
  const entry = { key: payload.idempotency_key, payload };

  if (replayPool.keys.length >= 500) {
    const oldest = replayPool.keys.shift();
    delete replayPool.entries[oldest];
  }
  replayPool.keys.push(payload.idempotency_key);
  replayPool.entries[payload.idempotency_key] = entry;
  return entry;
}

export function summaryFor(data, name) {
  const metrics = data.metrics;
  const duration = metrics.http_req_duration.values;
  const failed = metrics.http_req_failed.values;
  const summary = {
    scenario: name,
    run_id: __ENV.RUN_ID || 'local',
    requests: metrics.http_reqs.values.count,
    duration_s: Number((data.state.testRunDurationMs / 1000).toFixed(1)),
    rps: Number(metrics.http_reqs.values.rate.toFixed(2)),
    latency_ms: {
      p50: duration['p(50)'],
      p95: duration['p(95)'],
      p99: duration['p(99)'],
      max: duration.max,
    },
    error_rate: failed.passes === 0 ? 0 : failed.rate,
  };
  return {
    [`results/${name}.json`]: JSON.stringify(summary, null, 2),
    stdout:
      `\n=== ${name} ===\n` +
      `requests: ${summary.requests} in ${summary.duration_s}s (${summary.rps} rps)\n` +
      `latency ms: p50=${summary.latency_ms.p50.toFixed(1)} ` +
      `p95=${summary.latency_ms.p95.toFixed(1)} ` +
      `p99=${summary.latency_ms.p99.toFixed(1)} max=${summary.latency_ms.max.toFixed(1)}\n` +
      `error rate: ${summary.error_rate.toFixed(4)}\n`,
  };
}
