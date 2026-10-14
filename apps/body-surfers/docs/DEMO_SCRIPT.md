# Demo script

## 30-second elevator pitch

"This is Body Surfers. It's Subway Surfers, but you're the controller. The laptop webcam
tracks your body: step left or right to change lanes, jump to jump, squat to roll. There's
nothing to install and nothing to hold. Raise your hands to start, and if a friend steps in,
you race each other in split-screen on the same track. When you crash, it takes a selfie of
your crash face for the leaderboard. Everything runs locally in the browser, including the
pose tracking, and all the art and sound is generated in code. Want to try?"

## 2-minute judge demo

Setup before judges arrive: game fullscreen (F) in the lobby, autopilot runner surfing
behind the title, Hall of Fame on the second screen, tape mark on the floor about 2 m away.

| Time | Do | Say |
|---|---|---|
| 0:00 | Stand in front of the laptop, wave. Point at the webcam overlay. | "Body Surfers is Subway Surfers played with your whole body. That skeleton is me. The camera is the only controller." |
| 0:10 | Raise both hands, let the ring fill. | "Menus are hands-free. Raise both hands to start." |
| 0:15 | Stand still through 3-2-1. | "The countdown calibrates to my body and distance, so it works for anyone, tall or short, near or far." |
| 0:20 | Play through the tutorial: jump, squat, step left/right. | "Jump for barriers, squat to roll under bars, and where I stand is my lane. The green and orange lines on the overlay are my jump and squat lines." |
| 0:50 | Keep playing, grab a power-up or ramp if one comes. | "There are ramps onto train roofs and eight power-ups, including a jetpack and a star that smashes through obstacles. The speed keeps ramping up for the whole run." |
| 1:05 | Invite a judge: "Want to race?" Restart with both of you raising hands. | "If a second person is in frame, they get four seconds to raise their hands and join. Same track, split-screen." |
| 1:15 | Race. | "Each player's identity is locked once they join, so people walking behind us are ignored." |
| 1:35 | Someone crashes. Point at the selfie and the second screen. | "When you crash, it grabs your crash face. That's the leaderboard, and the Hall of Fame on the other screen updates live." |
| 1:45 | Step aside. | "It's a static web app, pose tracking runs on-device with MediaPipe, and the video never leaves the laptop. We built it with a contract-first, multi-agent workflow in Claude Code: frozen shared types, one agent per module, automated screenshot and smoke tests." |
| 2:00 | Done. | "Questions?" |

## Fallbacks

Use these if the camera, lighting or space fails. Do not debug in front of judges; switch.

1. **Keyboard.** Keyboard input is always live. Press 1 (or Enter) in the lobby for 1P, 2 for
   2P. Arrows or WASD to move, Up/W/Space to jump, Down/S/Shift to roll. In 2P: P1 uses
   WASD + Space + Left Shift, P2 uses arrows + Right Shift.
2. **Camera blocked or denied.** Reload with `?input=keyboard`.
3. **Hands-off showcase.** Open
   `http://localhost:5173/?input=keyboard&autostart=1&autopilot=1&players=2` and narrate
   while the AI races both sides. Add `&seed=7` (or any number) for a repeatable course.
4. **Tracking is jumpy.** Press `` ` `` for settings: raise smoothing and lane hysteresis,
   or switch the model to `full`. Check for a bright window behind the player.
5. **Slow frame rate.** Settings → quality `low`, model `lite`.
6. **Live build.** If the local setup fails entirely, open https://body-surfers-steel.vercel.app
   in Chrome.
