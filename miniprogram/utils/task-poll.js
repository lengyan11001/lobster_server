// 统一任务轮询：指数退避 + 页面隐藏暂停，避免高频打服务器
const STEPS = [3000, 6000, 10000, 15000, 20000];

function createPoller(fetchFn, onData, onError) {
  let timer = null;
  let step = 0;
  let stopped = false;
  let paused = false;

  const schedule = (delay) => {
    if (stopped) return;
    timer = setTimeout(tick, delay);
  };

  const tick = () => {
    if (stopped) return;
    if (paused) { schedule(1000); return; }
    Promise.resolve()
      .then(() => fetchFn())
      .then((data) => {
        const done = onData ? onData(data) : false;
        if (done) { stop(); return; }
        step = Math.min(step + 1, STEPS.length - 1);
        schedule(STEPS[step]);
      })
      .catch((err) => {
        step = Math.min(step + 1, STEPS.length - 1);
        if (onError) onError(err);
        schedule(STEPS[step]);
      });
  };

  const stop = () => { stopped = true; if (timer) clearTimeout(timer); timer = null; };
  const pause = () => { paused = true; };
  const resume = () => { paused = false; };

  return {
    start(immediate) { step = 0; stopped = false; if (immediate) tick(); else schedule(STEPS[0]); },
    stop, pause, resume,
  };
}

module.exports = { createPoller };
