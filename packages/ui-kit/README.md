# packages/ui-kit — shared visual components

React component library for all door-ui surfaces (T-004 seeds it; Gemini UI tasks extend it).

- **Design targets:** readable from hallway distance on the wallboard (large type, high contrast), fat-finger-friendly on the 7" DoorPad (min 48 px touch targets), graceful on both landscape and portrait.
- **Core components:** dashboard tile (with `as_of` staleness indicator), status badge (the eight presence labels with fixed colors/icons), big-button, QR code display, countdown/auto-reset wrapper, video player wrapper (from media-client), toast/feedback, greeting banner (generic + profile-colored variants).
- **Theming:** CSS variables, dark default (it's a dorm hallway), per-profile accent colors keyed by `profile_id`.
- **Safety:** every text-rendering component escapes content; no HTML-injection props on anything reachable from user-generated content.
- Storybook (or Ladle) catalog so Gemini UI tasks can be verified visually without hardware.
