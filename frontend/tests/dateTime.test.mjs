import assert from "node:assert/strict";
import test from "node:test";
import { formatCreatedAt } from "../src/dateTime.ts";

test("recording timestamps use Beijing time regardless of the machine timezone", () => {
  const prior = process.env.TZ;
  try {
    process.env.TZ = 'America/Los_Angeles';
    assert.equal(formatCreatedAt('2026-11-19T15:20:28.374023+00:00'), '2026-11-19 23:20:28（北京时间）');
    assert.equal(formatCreatedAt('2026-11-19T16:05:00Z'), '2026-11-20 00:05:00（北京时间）');
    assert.equal(formatCreatedAt('2026-11-19T23:20:28+08:00'), '2026-11-19 23:20:28（北京时间）');
    assert.equal(formatCreatedAt('2026-11-19T15:20:28'), '2026-11-19 23:20:28（北京时间）');
  } finally {
    if (prior === undefined) delete process.env.TZ;
    else process.env.TZ = prior;
  }
});

test("invalid timestamps remain available for inspection", () => {
  assert.equal(formatCreatedAt('未知日期'), '未知日期');
});
