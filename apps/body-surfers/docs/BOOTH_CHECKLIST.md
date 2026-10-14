# Booth checklist

## The night before

- [ ] `npm install && npm run build` succeeds. `bash scripts/check.sh` passes with the dev server running.
- [ ] Test `npx vite preview` (production build) with the real camera, 1P and 2P.
- [ ] Turn wifi off and confirm the game still loads and tracks (everything is self-hosted).
- [ ] Optional: record a backup gameplay video.
- [ ] Charge the laptop. Pack the charger, an extension cord, an HDMI/USB-C adapter for the
      second screen, and painter's tape.
- [ ] Optional: iPhone with Continuity Camera set up (same Apple ID, Wi-Fi and Bluetooth on).

## Setup at the booth

- [ ] Plug in power. Disable sleep, screen saver, notifications and Do Not Disturb exceptions.
- [ ] Close Zoom, Meet, Photo Booth, FaceTime and anything else that might grab the camera.
- [ ] Start the server: `npm run dev` or `npx vite preview`. Open the game in Chrome.
- [ ] Click **TAP TO START** once (unlocks audio and camera). Allow camera.
- [ ] Press F for fullscreen.
- [ ] Open `hall.html` in a second window on the second screen, fullscreen.
- [ ] Laptop at waist height or raised, screen tilted back so a standing player's head has
      headroom in the overlay.
- [ ] Main light source in front of the players. No bright window behind them.
- [ ] Tape a standing spot about 2 m from the laptop. For 2P, two spots about 1.5 m apart.
- [ ] Clear the floor around the spots. Nothing to trip over, nothing breakable within arm's reach.
- [ ] Settings (`` ` ``): select the camera, check model and quality, check the tuning sliders.
- [ ] Do one full 1P run and one 2P run yourself.
- [ ] Settings → Clear leaderboard before judging starts.
- [ ] Set volume so it is audible but not shouting over neighbors.

## During the event

- [ ] Remind each player: "Stand on the tape, stand still during the countdown, raise both hands to play again."
- [ ] Ask before keeping someone's selfie on screen; delete or clear if asked.
- [ ] Watch the debug overlay (`I` or `?debug=1`) if it feels laggy.
- [ ] If lighting changes (sunset, lights off), recheck tracking and adjust tuning.
- [ ] Keep the fallback URL ready:
      `http://localhost:5173/?input=keyboard&autostart=1&autopilot=1&players=2`
- [ ] Have water nearby. Players get tired after 2 to 3 runs.

## Talking points

- Your body is the controller; no install, no hardware beyond a webcam.
- Calibrates to each player during the countdown.
- 2-player split-screen race on the identical course.
- Pose tracking runs in the browser and video never leaves the laptop. Crash selfies stay in
  localStorage only. (MediaPipe's library does send anonymous usage pings to Google.)
- All art, music and sound are procedural. Runs offline.
- Built contract-first with parallel Claude Code agents.

## Teardown

- [ ] Settings → Clear leaderboard (deletes all selfies).
- [ ] Quit the browser and stop the server.

## Camera effects (important on M-series MacBooks)
- Turn OFF Center Stage and Studio Light: Control Center → Video Effects, while the camera is in use. Center Stage pans the image to follow you, which breaks the left/center/right lane zones.
- Check the light: if the webcam drops below ~24 fps in a dim room, jumps get laggy and blurry. Face a lamp or window.
