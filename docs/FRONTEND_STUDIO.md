# Cabinet visual system

The November 2026 refresh keeps plain JavaScript, the existing API, role boundaries,
section order and action flow. It updates shared styles rather than adding a UI framework.

## Direction

- Original matte obsidian texture with sparse gold mineral veins, softened by quiet
  warm/cool light fields. The locally hosted WebP is decorative, with a graphite fallback.
- Locally hosted Forum display typography and Google Sans reading/control text.
- Warm gold identifies primary actions, selection and progress.
- Translucent panels reveal the wallpaper through 22px blur with restrained saturation,
  a bright rim and a shaded lower edge. Sticky chrome uses 26px blur; dialogs retain
  a dense reading surface. Nested cards avoid a second blur layer.
- Unsupported backdrop filters retain opaque defaults. Reduced transparency or increased contrast selects
  opaque surfaces; reduced motion disables motion while retaining static glass.
  Forced colors retains visible boundaries.
- No external image/font requests, CDN, runtime package, animation framework or tracking.
  `webapp/backgrounds/obsidian-gold-v1.webp` is an original generated material texture;
  found marble photographs served as visual research and were not copied into the app.

## Layout and interactions

The header keeps identity and actions in separate flex regions. Full names remain
available in the profile; the compact header has a two-line limit and title text.
Current section names provide a semantic page heading. Desktop reports/tasks/profile
use bounded widths, the dashboard uses two columns and Academy members use a grid.
The mobile Academy portrait is compact so progress and tasks appear earlier.

Dialogs have a labelled dialog role, keyboard focus containment, Escape dismissal,
focus return and an inert background. Unambiguous field labels are associated with
their existing control. The scrollable analytics table is keyboard focusable.
Clickable task/list rows support Enter and Space. Native dates, phone verification,
downloads, existing submit-once protection and authorization are unchanged.

Avatar videos have still posters and reduced-motion rendering uses the static image.
Responsive behavior must preserve Russian names, multiline labels and horizontal
action text, including on a 320px viewport.

## Verification

Run JavaScript syntax and CSS parsing checks, then exercise the application with an
isolated synthetic database. Cover member, corporant, leadership, coordinator and
registration screens at 320/390/768/1440px. Inspect real screenshots in addition to
automated accessibility checks. Check dialog keyboard behavior, articles/back,
calendar, task/event forms, search, reports and cabinet switching.

Viewport emulation does not prove native Telegram/VK WebView behavior. Automated
axe checks do not certify full WCAG compliance or backend security. No production
identities or secrets belong in preview fixtures or saved screenshots.
