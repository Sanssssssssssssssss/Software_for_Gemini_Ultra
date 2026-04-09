# Gemini Internal Frontend DESIGN.md

## 1. Visual Theme & Atmosphere

This interface should feel like a modern internal AI operations console: dark, focused, quiet, and highly legible under long usage sessions. It is not a marketing site and not a playful chatbot skin. The mood should communicate trust, control, and technical clarity for operators who manage sessions, uploads, streaming output, and account health.

The overall experience combines the restraint of Vercel, the dark product discipline of Supabase, and the AI-console energy of VoltAgent, but removes flashy glow effects and decorative noise. Surfaces should feel intentionally engineered, with contrast coming from structure, spacing, and hierarchy rather than heavy gradients or glass blur.

## 2. Color Palette & Roles

### Core Surfaces

- `Console Black` `#0b1016`: page background
- `Panel Base` `#101720`: primary card and shell background
- `Panel Raised` `#15202b`: secondary elevated surfaces
- `Panel Soft` `#1a2633`: hover state and tertiary panels
- `Line Strong` `#273547`: primary border
- `Line Soft` `#1f2a38`: subtle divider

### Text

- `Text Primary` `#edf3f8`: main headings and body
- `Text Secondary` `#9fb0c0`: supporting copy
- `Text Muted` `#6f8091`: metadata and timestamps
- `Text Inverse` `#081018`: text on bright accent surfaces

### Accent

- `Signal Green` `#2fd38a`: primary accent and success-forward action
- `Signal Green Deep` `#1ca46a`: pressed or denser accent state
- `Signal Green Soft` `rgba(47, 211, 138, 0.14)`: subtle tint
- `Focus Blue` `#5aa9ff`: keyboard focus ring only

### Semantic

- `Warning Amber` `#ffb454`
- `Warning Soft` `rgba(255, 180, 84, 0.14)`
- `Danger Coral` `#ff6b6b`
- `Danger Soft` `rgba(255, 107, 107, 0.14)`
- `Info Cyan` `#58c4dd`
- `Info Soft` `rgba(88, 196, 221, 0.14)`

### Usage Rules

- Use green as the single brand signal.
- Use blue only for focus states and link emphasis when contrast needs help.
- Avoid purple, neon rainbow gradients, and warm orange hero accents.
- Keep backgrounds dark and matte, not glossy.

## 3. Typography Rules

### Font Family

- Sans: `"Inter", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif`
- Mono: `"Cascadia Code", "SFMono-Regular", Consolas, monospace`

### Hierarchy

| Role | Font | Size | Weight | Line Height | Letter Spacing | Notes |
|------|------|------|--------|-------------|----------------|-------|
| Hero | Sans | 52px | 700 | 1.02 | -0.05em | Reserved for top-level page statement |
| Page Title | Sans | 34px | 700 | 1.08 | -0.04em | Main route heading |
| Section Title | Sans | 22px | 650 | 1.18 | -0.02em | Panels and module headings |
| Card Title | Sans | 18px | 600 | 1.28 | -0.01em | Cards and list items |
| Body | Sans | 15px | 450 | 1.65 | normal | Standard reading text |
| Label | Sans | 13px | 600 | 1.4 | 0.01em | Inputs, badges, meta labels |
| Meta | Sans | 12px | 500 | 1.4 | 0.02em | Timestamps and secondary metrics |
| Code | Mono | 13px | 400 | 1.55 | normal | Inline and block code |

### Principles

- Keep headings dense and compact, but not theatrical.
- Use semibold labels to organize complex operator UIs.
- Preserve high readability for mixed English and Chinese text.
- Use monospace only where technical credibility helps: code, IDs, asset state, session snippets.

## 4. Component Stylings

### Shells and Panels

- Background: `Panel Base` or `Panel Raised`
- Border: `1px solid Line Strong`
- Radius: `20px` for major shells, `14px` for cards, `10px` for compact controls
- Shadow: `0 18px 48px rgba(0, 0, 0, 0.28)` for major shells only
- Avoid blur-heavy glassmorphism

### Buttons

Primary button:
- Background: linear gradient from `Signal Green` to `Signal Green Deep`
- Text: `Text Inverse`
- Radius: `999px`
- Height: `46-50px`
- Weight: `700`

Secondary button:
- Background: transparent or `Panel Soft`
- Text: `Text Primary`
- Border: `1px solid Line Strong`
- Radius: `999px`

Destructive button:
- Background: `Danger Soft`
- Text: `Danger Coral`
- Border: `1px solid rgba(255, 107, 107, 0.25)`

### Inputs

- Background: `#0e151d`
- Text: `Text Primary`
- Border: `1px solid Line Strong`
- Radius: `12px`
- Focus: `Focus Blue` ring at 3-4px soft spread
- Placeholder: `Text Muted`

### Session Rail

- Narrower, denser, and calmer than the main chat area
- Active session uses a green-tinted border and slightly raised background
- Preview text should remain muted and compact

### Message Cards

- User message: left accent rule in `Signal Green`
- Assistant message: neutral border with slightly brighter panel fill
- System message: subdued panel with muted type
- Code blocks: darker inset surface, clear border, no bright fills

### Metrics and Admin Cards

- Dense stat layout with strong numeric hierarchy
- Use semantic pill badges for state
- Avoid oversized hero treatment inside operational panels

## 5. Layout Principles

### Spacing

- Base unit: `8px`
- Primary scale: `8, 12, 16, 20, 24, 32, 40, 48`
- Prefer tighter gaps inside dense panels and larger spacing between major shells

### Structure

- App max width: `1360px`
- Use clear shell-in-shell hierarchy
- Chat page should prioritize vertical reading flow and stable composer placement
- Admin page should support quick scan across cards, metrics, and recent activity

### Empty States

- Keep empty states instructive and brief
- One supporting icon or mark is enough
- Do not turn empty states into marketing heroes

## 6. Depth & Elevation

| Level | Treatment | Use |
|-------|-----------|-----|
| Flat | background only | page canvas |
| Contained | border + matte panel | default cards |
| Raised | stronger panel + shell shadow | primary shells |
| Active | green-tinted border + slight lift | selected and current state |

Depth should come from panel contrast, border strength, and only light shadowing. Avoid multiple layered glows.

## 7. Do's and Don'ts

### Do

- Keep the app unmistakably product-like
- Use dark matte surfaces with strong text contrast
- Preserve calm, high-density layouts for chat and admin
- Centralize tokens before editing individual pages
- Use one green accent family across the app
- Keep code and markdown rendering comfortable for long reading

### Don't

- Do not use warm beige, paper textures, or heavy frosted-glass styling
- Do not add purple accents or generic AI rainbow gradients
- Do not over-round cards or inputs
- Do not make admin surfaces look like marketing sections
- Do not reduce contrast to chase subtlety

## 8. Responsive Behavior

- Collapse two-column shells to one column below `1100px`
- Preserve composer and action clarity on mobile
- Keep touch targets at least `44px`
- Allow rails and metadata lists to stack without losing hierarchy

## 9. Agent Prompt Guide

Example prompts:

- "Restyle the chat workspace using Gemini Console DESIGN.md. Keep streaming readability high and push most changes into shared tokens and surfaces."
- "Refactor the admin page to match Gemini Console: denser metric cards, calmer status pills, dark matte shells, and a single green accent family."
- "Apply the Gemini Console design system to login and setup without changing auth logic or route structure."
