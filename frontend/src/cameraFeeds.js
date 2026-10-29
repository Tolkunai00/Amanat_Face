// Each page owns its streams, retry timers, and browser event listeners.
export function connectCameraFeeds(images, baseUrl, browser = window, endpoint = "feed") {
  let disposed = false;
  const feeds = Array.from(images, (image) => {
    let timer = null;
    let delay = 1000;
    const reconnect = () => {
      if (disposed) return;
      browser.clearTimeout(timer);
      timer = null;
      image.src = `${baseUrl}/api/v1/camera/${endpoint}?status=${encodeURIComponent(image.dataset.cameraFeed)}&t=${Date.now()}`;
    };
    const failed = () => {
      if (disposed || timer !== null) return;
      timer = browser.setTimeout(reconnect, delay);
      delay = Math.min(delay * 2, 30000);
    };
    const loaded = () => { delay = 1000; };
    image.addEventListener("error", failed);
    image.addEventListener("load", loaded);
    return {
      image, reconnect,
      dispose() {
        browser.clearTimeout(timer);
        image.removeEventListener("error", failed);
        image.removeEventListener("load", loaded);
        image.removeAttribute("src");
      },
    };
  });
  const resume = () => {
    if (!browser.document.hidden) feeds.forEach((feed) => feed.reconnect());
  };
  browser.addEventListener("online", resume);
  browser.document.addEventListener("visibilitychange", resume);
  return {
    retry(status) { feeds.find((feed) => feed.image.dataset.cameraFeed === status)?.reconnect(); },
    dispose() {
      disposed = true;
      feeds.forEach((feed) => feed.dispose());
      browser.removeEventListener("online", resume);
      browser.document.removeEventListener("visibilitychange", resume);
    },
  };
}
