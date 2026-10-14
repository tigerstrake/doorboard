// A controllable real video fixture: scene.js renders a person, canvas.captureStream
// feeds the app's actual video/MediaPipe pipeline. No pose results or game inputs are mocked.
export async function installBodyCamera(ctx) {
  await ctx.context.addInitScript(({ base }) => {
    const media = navigator.mediaDevices;
    const realGetUserMedia = media.getUserMedia.bind(media);
    let makeStream;
    media.getUserMedia = async (constraints = {}) => {
      if (!constraints.video) return realGetUserMedia(constraints);
      if (!makeStream) {
        makeStream = (async () => {
          const { createWorld } = await import(`${base}/tools/fakecam/scene.js`);
          const scene = createWorld({ routine: 'solo', width: 1280, height: 720 });
          const canvas = document.createElement('canvas');
          canvas.width = 1280;
          canvas.height = 720;
          const draw = canvas.getContext('2d');
          const state = window.__bodyCamera = { visible: false, time: 0, rate: 0, at: performance.now() };
          const tick = () => {
            if (state.visible) {
              scene.renderAt(state.time + (performance.now() - state.at) / 1000 * state.rate);
              draw.drawImage(scene.canvas, 0, 0);
            } else {
              draw.fillStyle = '#cfc8bc';
              draw.fillRect(0, 0, canvas.width, canvas.height);
            }
            requestAnimationFrame(tick);
          };
          tick();
          return () => canvas.captureStream(30);
        })();
      }
      return (await makeStream)();
    };
    media.enumerateDevices = async () => [{ deviceId: 'body-camera', groupId: 'body-camera', kind: 'videoinput', label: 'Synthetic body camera' }];
  }, { base: ctx.base });
}

export async function setBodyCamera(ctx, { visible = true, time = 0, rate = 0 } = {}) {
  await ctx.page.evaluate((patch) => Object.assign(window.__bodyCamera, patch, { at: performance.now() }), { visible, time, rate });
}
