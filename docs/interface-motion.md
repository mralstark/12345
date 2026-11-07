# Interface patterns — 7 November 2026

The application remains a plain JavaScript Telegram Mini App / website. Keep
the existing sections, permissions, workflows, Forum headings and bundled Roboto
body text. Graphite, black and restrained gold; translucent surfaces without
reflections, blue tint or decorative shimmer.

## References and installed tools

- [Apple design resources](https://developer.apple.com/design/resources/) and
  [motion guidance](https://developer.apple.com/design/human-interface-guidelines/motion):
  spatial continuity, readable hierarchy, restrained movement and accessibility.
  This is design reference material, not an installable web plugin. No Apple fonts,
  proprietary icon assets or native UI kits are redistributed.
- [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill): already
  installed locally. Use focused searches; preserve this project's visual choices.
- [shadcn/ui skill](https://ui.shadcn.com/docs/skills): installed locally. Applicable
  to future React components; it is not a runtime dependency of this application.
- [Expo skills](https://github.com/expo/skills): installed expo-native-ui,
  expo-design-system and expo-ui. Reserved for an actual Expo/native project.
  Installing these skills does not migrate the web application to React Native.

## Shared patterns

- Floating bottom navigation, five destinations maximum; full-height selected
  pill moves between buttons. The menu retains every destination and search.
- The page title precedes cabinet context. Four compact shortcuts on management
  overviews; reports remain the single entry point for related downloads.
- Dense methodology explanations use disclosure controls, not permanently
  expanded introductory paragraphs.
- Content entrances: 280 ms, 6–14 px; selection: 320 ms; sheets: 360 ms in,
  180 ms out; press feedback: 160 ms. Transform/opacity only. No scroll takeover.
- Data loading does not consume the content entrance animation. A failed load
  offers retry; stale requests cannot replace a newer selected section.
- Dialog cleanup runs immediately; only the visual shell exits asynchronously.
  New dialogs cancel the old shell safely. Focus stays within the dialog and
  returns to its activator, including Safari pointer activation.
- Sheets fit the visible viewport when the software keyboard is displayed;
  pinch zoom stays browser-controlled. Desktop dialogs are centered.
- Reduced motion disables entrances, exits, press transforms and animated
  scrolling. Increased contrast/reduced transparency retains opaque fallbacks.
- No new network services, client secrets, third-party scripts or CSS CDN.

## Verification

Synthetic profiles: corporant, member, alumnus, cell leader, regional leader,
regional coordinator, federal coordinator and combined federal/superuser.
63 role-specific sections in Chromium, at 320/390/768/1440 widths; focus trapping,
return focus, reduced motion and navigation pill resizing. Additional WebKit
checks: coordinator and corporant, 15 sections and the same viewport widths.
15 Excel/PDF/CSV report downloads. Error retry, stale response suppression,
interrupted dialog closing and small keyboard viewport tested. Axe A/AA checks
on management overview and section menu. 72 relevant backend tests passed.

WebKit on Windows and viewport simulation do not substitute for testing the
physical iOS/Android Telegram clients. No claim of native iOS behavior parity.
