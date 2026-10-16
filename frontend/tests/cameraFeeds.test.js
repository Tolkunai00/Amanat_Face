import test from "node:test";
import assert from "node:assert/strict";
import { connectCameraFeeds } from "../src/cameraFeeds.js";

function fixture() {
  const timers = new Map();
  let nextTimer = 0;
  const browser = Object.assign(new EventTarget(), {
    document: Object.assign(new EventTarget(), { hidden: false }),
    setTimeout(fn, delay) { timers.set(++nextTimer, { fn, delay }); return nextTimer; },
    clearTimeout(id) { timers.delete(id); },
  });
  const image = Object.assign(new EventTarget(), {
    dataset: { cameraFeed: "keldi" }, src: "/initial-feed",
    removeAttribute() { this.src = ""; },
  });
  const feeds = connectCameraFeeds([image], "", browser);
  return { timers, browser, image, feeds };
}

test("retries a failed stream once with bounded backoff", () => {
  const { image, timers, feeds } = fixture();
  for (let attempt = 0; attempt < 8; attempt++) {
    image.dispatchEvent(new Event("error"));
    image.dispatchEvent(new Event("error"));
    assert.equal(timers.size, 1);
    const [{ fn, delay }] = timers.values();
    assert.equal(delay, Math.min(1000 * 2 ** attempt, 30000));
    fn();
    assert.equal(timers.size, 0);
    assert.match(image.src, /^\/api\/v1\/camera\/feed\?status=keldi&t=\d+$/);
  }
  image.dispatchEvent(new Event("load"));
  image.dispatchEvent(new Event("error"));
  assert.equal([...timers.values()][0].delay, 1000);
  feeds.dispose();
});

test("leaving the camera page closes streams and cancels all retries", () => {
  const { image, browser, timers, feeds } = fixture();
  image.dispatchEvent(new Event("error"));
  const queued = [...timers.values()][0].fn;
  feeds.dispose();
  queued();
  image.dispatchEvent(new Event("error"));
  browser.dispatchEvent(new Event("online"));
  browser.document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(image.src, "");
  assert.equal(timers.size, 0);
});

test("returning to the tab or reconnecting the network opens a fresh stream", () => {
  const { image, browser, feeds } = fixture();
  browser.document.hidden = true;
  browser.document.dispatchEvent(new Event("visibilitychange"));
  assert.equal(image.src, "/initial-feed");
  browser.document.hidden = false;
  browser.document.dispatchEvent(new Event("visibilitychange"));
  assert.match(image.src, /status=keldi/);
  image.src = "/broken";
  browser.dispatchEvent(new Event("online"));
  assert.match(image.src, /status=keldi/);
  feeds.dispose();
});
