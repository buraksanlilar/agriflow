---
name: AgriFlow
colors:
  surface: '#fcf8ff'
  surface-dim: '#dbd8e4'
  surface-bright: '#fcf8ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f5f2fe'
  surface-container: '#efecf8'
  surface-container-high: '#e9e6f3'
  surface-container-highest: '#e4e1ed'
  on-surface: '#1b1b23'
  on-surface-variant: '#464554'
  inverse-surface: '#303038'
  inverse-on-surface: '#f2effb'
  outline: '#767586'
  outline-variant: '#c7c4d7'
  surface-tint: '#494bd6'
  primary: '#4648d4'
  on-primary: '#ffffff'
  primary-container: '#6063ee'
  on-primary-container: '#fffbff'
  inverse-primary: '#c0c1ff'
  secondary: '#575992'
  on-secondary: '#ffffff'
  secondary-container: '#bdbefe'
  on-secondary-container: '#494b83'
  tertiary: '#904900'
  on-tertiary: '#ffffff'
  tertiary-container: '#b55d00'
  on-tertiary-container: '#fffbff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#07006c'
  on-primary-fixed-variant: '#2f2ebe'
  secondary-fixed: '#e1e0ff'
  secondary-fixed-dim: '#c0c1ff'
  on-secondary-fixed: '#13144a'
  on-secondary-fixed-variant: '#404178'
  tertiary-fixed: '#ffdcc5'
  tertiary-fixed-dim: '#ffb783'
  on-tertiary-fixed: '#301400'
  on-tertiary-fixed-variant: '#703700'
  background: '#fcf8ff'
  on-background: '#1b1b23'
  surface-variant: '#e4e1ed'
typography:
  display:
    fontFamily: Inter
    fontSize: 30px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.02em
  h1:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  h2:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
    letterSpacing: -0.01em
  body:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: '0'
  body-sm:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
    letterSpacing: '0'
  data-lg:
    fontFamily: JetBrains Mono
    fontSize: 18px
    fontWeight: '500'
    lineHeight: 24px
    letterSpacing: '0'
  data-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: '0'
  label:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.02em
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
  3xl: 64px
---

## Brand & Style

The design system is engineered for precision, reliability, and clarity. It avoids traditional agricultural tropes—specifically rejecting green tones and organic motifs—in favor of a high-performance, technical aesthetic. This design system communicates authority through a "Software-as-Infrastructure" approach, emphasizing the IoT and data-processing capabilities of the platform.

The visual language is strictly minimalist. It relies on mathematical alignment, generous whitespace, and a high-contrast functional palette to guide the user. The emotional response should be one of "calm control" and "technical excellence," moving the user away from the dirt and into the data.

## Colors

The color strategy is strictly monochromatic with a singular indigo accent. The background uses `Gray-50` to provide a subtle contrast against the `White` content containers. 

`Indigo-500` is reserved exclusively for primary actions, active states, and primary data series. There is a total prohibition on green; functional status (success) should be indicated via indigo or neutral tones with distinct iconography, rather than green hues. Secondary data series must utilize the neutral gray scale to maintain a clean, analytical environment.

## Typography

This design system utilizes a dual-font approach. **Inter** serves as the primary interface typeface, chosen for its exceptional legibility and neutral, modern tone. Headings use tight letter-spacing and semi-bold weights to mirror the aesthetic of high-end developer tools.

**JetBrains Mono** is mandated for all sensor readings, coordinates, timestamps, and numerical data points. This creates a clear visual distinction between "UI instruction" and "Machine data." All data labels should use Inter in a small, uppercase format to maintain a structured, technical hierarchy.

## Layout & Spacing

The layout is built on a rigorous 8px grid system. Consistency in spacing is paramount to achieving the "Linear-inspired" look. 

We employ a fixed-fluid hybrid grid: sidebars and navigation panels are fixed width, while the primary content area scales to fill the viewport until it reaches a maximum width of 1440px. Components must align to the grid, using `16px` (md) for internal container padding and `24px` (lg) for gaps between major sections. Whitespace is used as a functional tool to separate data sets without the need for heavy visual dividers.

## Elevation & Depth

This design system rejects all forms of shadows, gradients, and glassmorphism. Depth is communicated exclusively through **tonal layering** and **flat borders**.

1.  **Layer 0 (Background):** `Gray-50` serves as the canvas.
2.  **Layer 1 (Content Cards):** `White` background with a `1px` border of `Gray-200`.
3.  **Layer 2 (Popovers/Modals):** `White` background with a `1px` border of `Gray-300` to provide higher contrast against Layer 1.

Visual hierarchy is established by the density of the borders. Hover states on interactive elements should be indicated by a border color change (e.g., from `Gray-200` to `Indigo-500`) rather than a shadow or lift effect.

## Shapes

The shape language is structured and architectural. While based on a `0.5rem` (8px) core, cards are specifically defined at `12px` to provide a subtle softening of the data-heavy interface. Smaller interactive elements like buttons and inputs utilize a more disciplined `8px` radius.

All corners must be consistent across the platform. Sharp corners (0px) are only permitted for vertical/horizontal dividers and table row separators.

## Components

### Buttons
Buttons follow a flat, high-contrast style. Primary buttons use an `Indigo-500` background with white text. Secondary buttons use a `White` background with a `Gray-200` border. No shadows. On hover, primary buttons shift to `Indigo-600`, and secondary buttons shift to a `Gray-50` background.

### Cards
Cards are the primary container. They feature a `12px` border radius, `1px` solid border (`Gray-200`), and `White` background. No exceptions. Padding inside cards is strictly `24px`.

### Data Visualization (Recharts)
Charts must be minimalist. Use thin, `1.5px` stroke lines for line charts. No area fills or gradients. The primary series is `Indigo-500`. Secondary or comparison series use `Gray-400` and `Gray-200`. Grid lines within charts must be `Gray-100` and dashed.

### Input Fields
Inputs use a `Gray-50` background with an `8px` radius and a `1px` border. The border becomes `Indigo-500` on focus. Placeholder text must be `Gray-400`.

### Iconography
Icons must be geometric and use a `2px` stroke weight. Avoid all organic or literal agricultural shapes (no leaves, no tractors). Use abstract geometric symbols to represent system functions (e.g., a simple circle with a dot for a sensor, a square with arrows for flow).