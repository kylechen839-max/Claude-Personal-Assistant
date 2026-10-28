---
name: animation-vocabulary
description: Motion vocabulary and micro-interaction standards for Kwil's web UI - a glossary that turns a vague description of a motion effect into its exact term ("the bouncy thing when a popover opens" -> Pop in), mapped to plain CSS transitions and the Web Animations API with prefers-reduced-motion, plus easing, duration and press-feedback standards. Use when naming, adding, reviewing or toning down an animation or micro-interaction. No animation libraries.
license: MIT (see LICENSE)
---

# Animation Vocabulary (adapted for Kwil)

Adapted from emilkowalski/skills at commit
a81dbca07f2217b7103e510c0f13f8d5b017afc9 (MIT, see `LICENSE`):

- The glossary below is `skills/animation-vocabulary/SKILL.md`,
  verbatim. Its "Initial Response" section (a fixed greeting that tells
  the agent to say nothing else until asked) was dropped, since the
  designer loads this as a reference mid-task.
- `references/STANDARDS.md` is `skills/review-animations/STANDARDS.md`,
  verbatim: easing curves, durations, press feedback, interruptibility,
  stagger and reduced motion.
- The "In Kwil" section is new. It maps the terms onto plain CSS and the
  Web Animations API (WAAPI).

This replaces the GSAP skills Kyle's list mentioned: Kwil adds no
animation library (no GSAP, Framer Motion or React), and loads nothing
from a CDN.

## In Kwil

**Tools, in order of preference**
1. A CSS `transition` on a state change (class, `[data-state]`,
   `[open]`, `:active`). Interruptible, no JS.
2. A CSS `@keyframes` animation for an entrance that plays once
   (`rise`, `pop`, `sheet-in` in `kwil/web/app.css`) or a loop that
   means something (the recording ripple, the voice orb).
3. `element.animate(keyframes, options)` (WAAPI) only when the values are
   computed in JS (an origin-aware popover, a shared-element move). Set
   the end state on the element yourself; never rely on `finish` events
   for correctness.

All styles go in the stylesheets: Kwil's CSP blocks inline `style`
attributes and inline `<style>` and `<script>`. Setting
`element.style.transform` from a script file is fine.

**Motion tokens.** Match what `app.css` already uses before adding new
values: 120-160ms for press feedback and small popovers (`pop` .16s
ease-out), 200-240ms for messages and sheets (`rise` .22s, `sheet-in`
.24s `cubic-bezier(.2,.9,.3,1)`). New UI motion stays under 300ms and
uses ease-out to enter. If a value repeats, make it a custom property in
`:root` rather than a new literal.

**Reduced motion.** `app.css` already ends every animation and transition
at once under `@media (prefers-reduced-motion: reduce)`. So:
- content must be visible and correct with no animation at all (never
  start an element at `opacity: 0` and wait for an animation to reveal
  it);
- a WAAPI call checks
  `matchMedia('(prefers-reduced-motion: reduce)').matches` and skips the
  movement (jump to the end state, or fade only);
- a loop that carries state (listening, recording) keeps a static cue in
  reduced motion: a colour, an icon or text, not only movement.

**Which terms fit Kwil.** Fade, scale in from 0.95-0.97, slide in for
sheets, press feedback (`scale(.97)` on `:active`), origin-aware
popovers, crossfade, accordion, skeleton, tabular numbers, pulse for a
live state. Avoid in the app: parallax, scroll-driven reveals, marquee,
float or idle animation, typewriter, bounce on routine UI, and any
animation on keyboard-triggered actions. Hover motion is gated with
`@media (hover: hover) and (pointer: fine)`; on a phone a tap fires a
false hover.

**Where STANDARDS.md shows React or Framer Motion** (`useEffect`,
`useReducedMotion`, `motion.div`, `{ type: "spring" }`), apply the
principle with the CSS or WAAPI equivalent above. Its links (easing.dev,
easings.co) are for a human to browse; don't fetch them.

## Quick Start

The user describes an effect loosely. You return the matching term(s) in this format:

```
**Stagger** — Animate several items one after another with a small delay between each, creating a cascade.
```

If several terms could fit, list the best match first, then 1–2 alternates with a one-line note on how they differ.

## Instructions

1. **Read for intent, not keywords.** Users describe what they *see* or *feel* ("springy", "slides off", "draws itself in"), not the technical name. Map the sensation to the glossary below.
2. **Quote the glossary verbatim.** Its descriptions are authoritative — use them as-is, don't paraphrase.
3. **Disambiguate close terms.** When two compete (*Clip-path* vs *Mask*, *Pop in* vs *Bounce*, *Shared element transition* vs *Layout animation*), contrast them so the user can pick.
4. **When nothing matches exactly,** name the closest term and say plainly it's an approximation, or describe the effect in the glossary's vocabulary (e.g. "that's a *stagger* of *scale-in* entrances").
5. **Stay within this glossary.** If a term genuinely isn't here, say so rather than inventing one — though you may explain the concept using these words.
6. **Keep it tight.** A naming question wants a name, not an essay. Lead with the term; expand only if asked.

## Examples

**Example 1 — feel-based**
User: "What's it called when a popover seems to grow out of the button you clicked instead of from its middle?"
Output:
```
**Origin-aware animation** — An element animates out of its trigger, like a popover growing from the button that opened it instead of from its own center which is the default in CSS.
```

**Example 2 — disambiguation**
User: "The thing where one image turns into another image."
Output:
```
**Morph** — One shape smoothly turns into another shape, e.g. Dynamic Island.

Close alternates:
- **Crossfade** — if they simply fade over each other in the same spot.
- **Shared element transition** — if an element travels and transforms from one position into another.
```

**Example 3 — physics feel**
User: "That iOS scroll where it resists and snaps back when you pull too far."
Output:
```
**Rubber-banding** — Resistance and snap-back when you drag past a boundary (the iOS overscroll feel).
```

## Glossary

_A curated snapshot mirroring the project's `/vocabulary` page; keep the two in sync when either changes._

### Entrances & Exits — how elements appear and disappear
- **Fade in / Fade out** — Element appears or disappears by changing opacity.
- **Slide in** — Element enters by sliding in from off-screen (left, right, top, or bottom).
- **Scale in** — Element grows from smaller to full size as it appears, often paired with a fade.
- **Pop in** — Element appears with a slight overshoot, like it bounces into place.
- **Reveal** — Content is uncovered gradually, often by animating a clip-path or mask.
- **Enter / Exit** — The animation an element plays when it's added to or removed from the screen.

### Sequencing & Timing — coordinating multiple elements or moments
- **Keyframes** — Defined points in an animation (0%, 50%, 100%) that the browser fills the gaps between.
- **Interpolation / Tween** — Generating all the in-between frames between a start and end value, so motion is continuous.
- **Stagger** — Animate several items one after another with a small delay between each, creating a cascade.
- **Orchestration** — Deliberately timing multiple animations so they feel like one coordinated motion.
- **Delay** — Time before an animation starts.
- **Duration** — How long an animation takes.
- **Fill mode** — Whether an element keeps its first or last frame's styles before the animation starts or after it ends (e.g. forwards).
- **Stepped animation** — An animation that is divided into discrete steps, like a countdown timer.

### Movement & Transforms — changing an element's position, size, or angle
- **Translate** — Move an element along the X or Y axis.
- **Scale** — Make an element bigger or smaller.
- **Rotate** — Spin an element around a point.
- **Skew** — Slant an element along the X or Y axis, shearing it out of its rectangular shape.
- **3D tilt / Flip** — Rotate in 3D space (rotateX / rotateY) to add depth.
- **Perspective** — How strong the 3D effect looks — a lower value exaggerates depth, like the viewer is closer.
- **Transform origin** — The anchor point a scale or rotation grows or spins from.
- **Origin-aware animation** — An element animates out of its trigger, like a popover growing from the button that opened it instead of from its own center which is the default in CSS.

### Transitions Between States — connecting one state, view, or element to another
- **Crossfade** — One element fades out as another fades in, in the same spot.
- **Continuity transition** — A change that keeps the user oriented by visually connecting before and after. For example, making the same rectangle bigger and smaller.
- **Morph** — One shape smoothly turns into another shape, e.g. Dynamic Island.
- **Shared element transition** — An element travels and transforms from one position into another, like a thumbnail expanding into a card.
- **Layout animation** — When an element's size or position changes, it animates to the new spot instead of snapping.
- **Accordion / Collapse** — A section smoothly expands and collapses its height to show or hide content.
- **Direction-aware transition** — Content slides one way going forward and the opposite way going back, so navigation has a sense of direction.

### Scroll — motion tied to scrolling or navigating between views
- **Scroll reveal** — Elements fade or slide into place as they enter the viewport.
- **Scroll-driven animation** — An animation whose progress is tied directly to scroll position.
- **Parallax** — Background and foreground move at different speeds while scrolling, creating depth.
- **Page transition** — An animation that plays when navigating from one page or route to another.
- **View transition** — The browser morphs between two states or pages, connecting shared elements.

### Feedback & Interaction — responding to the user's actions
- **Hover effect** — Visual change when the cursor moves over an element.
- **Press / Tap feedback** — A subtle scale-down when an element is clicked, so it feels physical.
- **Hold to confirm** — A progress effect that fills up while the user holds a button.
- **Drag** — Moving an element by grabbing it, often with momentum when released.
- **Drag to reorder** — Dragging items in a list to rearrange them, while the others shift to make room.
- **Swipe to dismiss** — Dragging an element off-screen to close it, like a drawer or toast.
- **Rubber-banding** — Resistance and snap-back when you drag past a boundary (the iOS overscroll feel).
- **Shake / Wiggle** — A quick side-to-side jitter signaling an error or rejected input.
- **Ripple** — A circle expanding from the point of a tap, confirming the press.

### Easing — how speed changes over an animation
- **Easing** — The rate at which an animation speeds up or slows down.
- **Ease-out** — Starts fast, ends slow. The default for most UI and anything responding to the user.
- **Ease-in** — Starts slow, ends fast. Usually avoided; can feel sluggish.
- **Ease-in-out** — Slow, fast, slow. Good for elements already on screen moving from A to B.
- **Linear** — Constant speed. Avoid for UI; reserve for spinners or marquees.
- **Cubic-bezier** — A custom easing curve you define for precise control.
- **Asymmetric easing** — A curve that accelerates and decelerates at different rates. Feels more alive than a symmetric one.

### Spring Animations — physics-based motion as an alternative to fixed-duration easing
- **Spring** — Motion driven by physics (tension, mass, damping) rather than a set duration.
- **Stiffness / Tension** — How strongly the spring pulls toward its target. Higher feels snappier.
- **Damping** — How quickly a spring settles. Lower damping means more bounce and oscillation.
- **Mass** — How heavy the animated element feels. More mass makes it slower and more sluggish.
- **Bounce** — A spring that overshoots and settles, adding playfulness.
- **Perceptual duration** — How long a spring feels finished, even though it keeps micro-settling underneath.
- **Momentum** — Motion that carries velocity, especially after a drag or interruption.
- **Velocity** — How fast and in which direction an element is moving. A spring carries it into the next animation when interrupted, so a flicked element keeps its speed.
- **Interruptible animation** — An animation that can be smoothly redirected mid-flight instead of finishing first.

### Looping & Ambient Motion — animations that run on their own
- **Marquee** — Text or content that scrolls continuously in a loop.
- **Loop** — An animation that repeats, a set number of times or infinitely.
- **Alternate (yoyo)** — A loop that plays forward then reverses each iteration, instead of jumping back to the start.
- **Orbit** — An element circling around another in a continuous path.
- **Pulse** — A gentle repeating scale or opacity change to draw attention.
- **Float** — A gentle, continuous up-and-down drift that makes a static element feel alive and weightless.
- **Idle animation** — Subtle motion that plays while an element is just sitting there, waiting to be interacted with.

### Polish & Effects — the small touches that separate good from great
- **Blur** — A blur filter used to soften an element or mask tiny imperfections.
- **Clip-path** — Clipping an element to a shape, used for reveals, masks, and before/after sliders.
- **Mask** — Hiding or revealing parts of an element using a shape or gradient — like clip-path, but with soft, fadeable edges.
- **Before / after slider** — A draggable divider that wipes between two overlaid images to compare them.
- **Line drawing** — An SVG path that draws itself in, like an invisible pen tracing it.
- **Text morph** — Text that animates character by character when it changes, drawing attention to the new value.
- **Skeleton / Shimmer** — A placeholder with a moving sheen shown while content loads.
- **Number ticker** — Digits rolling or counting up to a value.
- **Tabular numbers** — Fixed-width digits so numbers don't shift around as they change. Essential for tickers, timers, and counters.
- **Typewriter** — Text appearing one character at a time, as if being typed.

### Performance — what keeps motion smooth instead of stuttering
- **Frame rate (FPS)** — Frames drawn per second. 60fps is the baseline for smooth motion; 120fps on newer displays.
- **Jank** — Visible stutter when the browser drops frames because it can't keep up with the animation.
- **Dropped frame** — A frame the browser missed its deadline to draw, causing a tiny hitch in motion.
- **Compositing** — Letting the GPU move or fade an element on its own layer without redoing layout or paint.
- **will-change** — A CSS hint that an element is about to animate, so the browser can promote it to its own layer ahead of time.
- **Layout thrashing** — Animating properties like width, height, top, or left that force the browser to recalculate layout every frame, causing jank.

### Principles to Know — concepts that guide when and how to animate
- **Purposeful animation** — Motion should serve a function — orient, give feedback, show relationships — not just decorate.
- **Anticipation** — A small wind-up in the opposite direction before a move, hinting at what's about to happen.
- **Follow-through** — Parts of an element keep moving and settle slightly after the main motion stops, adding weight.
- **Squash & stretch** — Deforming an element as it moves to convey weight, speed, and flexibility.
- **Perceived performance** — The right animation makes an interface feel faster, even when it isn't.
- **Frequency of use** — The more often a user sees an animation, the shorter and subtler it should be.
- **Spatial consistency** — Animating so an element keeps its identity and position across states, so users never lose track of where things went.
- **Hardware acceleration** — Animating transform and opacity lets the GPU keep motion smooth.
- **Reduced motion** — Respecting the user's prefers-reduced-motion setting by toning down or removing motion.
