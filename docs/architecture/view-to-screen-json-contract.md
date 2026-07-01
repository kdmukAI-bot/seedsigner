# How Views drive screens: the JSON screen contract

_Status: implemented on `integration/lvgl-mpy` (2026-07). Describes how the shared
Python app hands work to — and receives input from — the native LVGL screens. This is
the **Python side** of the contract; its native counterpart (what the C module
exposes) is documented in [lvgl-host-api-unification.md](lvgl-host-api-unification.md)._

## Who this is for

Anyone who needs to understand the boundary between SeedSigner's business logic
(Views) and the screen renderer:

- Working on Views and want to know how a screen gets shown and how the result comes
  back.
- Working on the native LVGL screens and want to know exactly what config shape the
  Python app sends.
- **Building an alternative screen backend** (e.g. a text-only display). The contract
  is renderer-agnostic: match the two ends described here and the entire View layer
  drives your backend unchanged. See [Building an alternative backend](#building-an-alternative-screen-backend).

Line numbers below are a convenience; **function names are the durable reference** if
the code shifts.

## The contract in one paragraph

A **View** never imports the native screen module and never builds JSON. It calls a
dispatch seam with a **screen *name* (a string)** plus flat keyword attrs. The seam
routes strings to a single **runner**, and the runner is the *only* place the JSON
`cfg` dict is shaped. The native screen function is a **pure builder** — it builds the
widget tree and returns immediately — and Python then **polls a result queue**. The
native side emits an **event tuple**, which the runner translates into a SeedSigner
**return code**, and that return code *is* the user's input handed back to the View.

```
View.run_button_list_screen(title=…, button_data=[ButtonOption(…), …])   # view layer, flat kwargs
      │
      ▼
View.run_screen("button_list_screen", **attrs)      # dispatch seam: str → native, class → legacy PIL
      │
      ▼
run_lvgl_screen(renderer, name, attrs)              # the runner
      │  _assemble_cfg(attrs)  ──►  cfg  (the JSON contract)
      │  <native>.button_list_screen(cfg)           # pure builder, returns immediately
      │  loop: <native>.poll_for_result()  ──►  ("button_selected", 2, "Scan") | None
      │  _translate_event(event)  ──►  2             # int index / RET_CODE / str
      ▼
return 2   →  back to the View's run()
```

So there are **two ends to match**: the `cfg` you consume (input) and the event tuples
you emit (output).

---

## 1. The dispatch seam — how a View asks for a screen

File: [src/seedsigner/views/view.py](../../src/seedsigner/views/view.py)

- [`View.run_screen`](../../src/seedsigner/views/view.py#L191-L228) — the
  backend-agnostic boundary. `isinstance(screen, type)` → a legacy PIL Screen
  *class*; a `str` → the native runner. This `type`-vs-`str` check is the whole
  PIL-vs-native discriminator, and it is MicroPython-safe (no PIL import on the native
  path).
- [`View.run_button_list_screen`](../../src/seedsigner/views/view.py#L231-L270) —
  typed entry point for **menu / list** screens. Its keyword signature *is* the
  list-screen input shape (title, button_data, show_back_button, top-nav icon,
  is_bottom_list, selected_button, button_style, checked_buttons, …).
- [`View.run_status_screen`](../../src/seedsigner/views/view.py#L291-L325) — typed
  entry point for the **status / warning / error** family. `status_type` owns the
  icon; title and confirm-button label default per status type.
- [`ButtonOption` / `ButtonOptionWithoutTranslation`](../../src/seedsigner/views/view.py#L21-L85)
  — the view-layer button vocab. [`resolved_label()`](../../src/seedsigner/views/view.py#L67-L73)
  is the *one and only* place button-label translation happens; by the time a label
  reaches the renderer it is already translated.
- [`RET_CODE__BACK_BUTTON = 1000` / `RET_CODE__POWER_BUTTON = 1001`](../../src/seedsigner/views/view.py#L16-L17)
  — the two sentinel return codes. Everything else a screen returns is an int button
  index or a str.

Views call the typed `run_*_screen` helpers wherever possible; bare `run_screen`
serves `main_menu_screen` and one-offs.

---

## 2. The runner — the authoritative JSON contract

File: [src/seedsigner/gui/lvgl_screen_runner.py](../../src/seedsigner/gui/lvgl_screen_runner.py)

Start with the [module docstring](../../src/seedsigner/gui/lvgl_screen_runner.py#L1-L33)
— it lays out "one contract, two mechanics" (CPython blended-display vs MicroPython
native task) and the screensaver-ownership model.

- [`_assemble_cfg`](../../src/seedsigner/gui/lvgl_screen_runner.py#L193-L248) — **the
  single source of truth for the JSON shape.** It nests `top_nav`, turns `button_data`
  → `button_list`, renames keys, **drops `None`** (the native side then applies its own
  default), and stamps `allow_screensaver`. To know exactly which keys land in the cfg,
  read this function.
- [`_serialize_button_option`](../../src/seedsigner/gui/lvgl_screen_runner.py#L162-L190)
  — how one `ButtonOption` becomes either a **bare label string** (no styling) or an
  object `{"label", "icon"?, "right_icon"?, "icon_color"?, "label_color"?}`.
- [`run_lvgl_screen`](../../src/seedsigner/gui/lvgl_screen_runner.py#L251-L330) — the
  driver. The [MicroPython branch](../../src/seedsigner/gui/lvgl_screen_runner.py#L288-L301)
  is the reference interaction pattern: `clear_result_queue()` → `screen_fn(cfg)` (pure
  builder) → loop on `poll_for_result()` → `_translate_event`. The native side owns the
  display, input, the LVGL pump, and the idle screensaver; Python only polls. (The
  CPython branch below it additionally pumps LVGL itself and routes pixels through the
  legacy PIL display driver — a transitional detail specific to the Pi Zero blended
  display, not part of the screen contract.)
- [`_translate_event`](../../src/seedsigner/gui/lvgl_screen_runner.py#L113-L137) — **the
  return contract (the other direction).**

### cfg keys (input)

What `_assemble_cfg` produces. A screen reads what it needs and ignores the rest;
omitted keys fall back to the native default.

| Key | Type | Notes |
|---|---|---|
| `top_nav` | object | `{title, show_back_button, show_power_button, icon, icon_color}` — only the sub-keys the View set are present |
| `button_list` | list | each item is a `str` **or** `{label, icon?, right_icon?, icon_color?, label_color?}` |
| `initial_selected_index` | int | renamed from the view's `selected_button`; which item starts focused |
| `allow_screensaver` | bool | always present; defaults `True`. A View sets `False` for screens that must stay up (e.g. camera scanning) |
| `text` | str | intro/body text (already translated) |
| `status_type` | str | status screens — see [`StatusType`](#3-string-valued-enums-in-the-cfg) |
| `status_headline` | str | status screens |
| `warning_edges` | bool | status screens |
| `is_bottom_list`, `is_button_text_centered` | bool | list layout |
| `button_style` | str | list style — see [`ButtonStyle`](#3-string-valued-enums-in-the-cfg) |
| `checked_buttons` | list[int] | which items render checked (checkbox/checked_selection styles) |

Any other flat key a View passes is copied through verbatim (again, `None` ⇒ omitted),
so screen-unique keys need no runner changes.

All strings arriving in the cfg are **already translated**. The renderer must not
translate — the sole `_()` calls live on the view side (`ButtonOption.resolved_label()`
and the status-screen title/button defaults).

### Event tuples (output)

What a screen must queue for `poll_for_result()` to return, and what each becomes:

| Native event tuple | Returned to the View |
|---|---|
| `("button_selected", index, label)` | `index` (int) |
| `("topnav_back", -1, "topnav_back")` | `RET_CODE__BACK_BUTTON` (1000) |
| `("topnav_power", -1, "topnav_power")` | `RET_CODE__POWER_BUTTON` (1001) |
| `("text_entered", -1, text)` | `text` (str) — e.g. a confirmed passphrase |

`poll_for_result()` returns `None` while the user hasn't acted yet.

---

## 3. String-valued enums in the cfg

File: [src/seedsigner/gui/constants.py](../../src/seedsigner/gui/constants.py)

- [`StatusType`](../../src/seedsigner/gui/constants.py#L152-L166) — `"success"`,
  `"warning"`, `"dire_warning"`, `"error"`. Selects the status screen's icon + color.
- [`ButtonStyle`](../../src/seedsigner/gui/constants.py#L169-L173) — `"default"`,
  `"checkbox"`, `"checked_selection"`. Button-list render style.

These string values are part of the wire contract — a backend's parser must accept the
same strings.

---

## Runtime hooks the runner expects

Beyond the per-screen builder functions, the runner calls a small set of module-level
functions (see [`ensure_lvgl_runtime`](../../src/seedsigner/gui/lvgl_screen_runner.py#L55-L99)
and `run_lvgl_screen`). A backend that wants to be driven by the existing runner
provides these; the import name it looks for is `seedsigner_lvgl_screens` (with an
interim `seedsigner_lvgl` fallback):

| Hook | Purpose |
|---|---|
| `init()` | Bring up runtime + display + input (MicroPython/on-device) |
| `set_screensaver_timeout(ms)` | Hand the idle timeout to the native overlay manager once at init |
| `<screen_name>(cfg)` | Build the screen and return immediately (pure builder) |
| `poll_for_result()` | Non-blocking; return an event tuple or `None` |
| `clear_result_queue()` | Drop any stale result before building the next screen |

(The CPython/Pi Zero blended-display path additionally uses `lvgl_init`,
`native_input_init`, `set_flush_mode`, `set_flush_callback`, and `lvgl_pump`. Those are
specific to sharing the panel with the legacy PIL pipeline and are **not** part of the
screen contract — a non-blended backend can ignore them.)

---

## Building an alternative screen backend

The contract is renderer-agnostic. To drive the existing View layer from a different
display technology (e.g. a text-only screen):

1. **Register screen functions by the names Views call** — at minimum
   `button_list_screen`, `large_icon_status_screen`, `main_menu_screen`.
2. **Parse the cfg exactly as `_assemble_cfg` emits it** (see the [cfg keys](#cfg-keys-input)
   table). Treat missing keys as your own default.
3. **Emit the four event tuples** from the [Event tuples](#event-tuples-output) table
   via `poll_for_result()` (returning `None` while waiting), and expose the
   [runtime hooks](#runtime-hooks-the-runner-expects).

Match the input keys and the output event tuples and no view-layer changes are needed.

---

## Design notes

- The JSON shape is **not** defined at call sites and **not** in Views — it is
  centralized in `_assemble_cfg` + `_serialize_button_option`. Read those two, not the
  dozens of call sites.
- Views identify a native screen **by name (a string)**, never by importing the native
  module. This keeps the business logic free of any renderer dependency and lets the
  flow-test harness patch `run_screen` without touching the native path.
- Translation is owned entirely on the view side; the runner and every screen backend
  receive already-translated strings.
