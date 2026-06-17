# Unified LVGL Host API: one Python surface on both platforms

_Status: design + partially implemented (2026-06-17). The ESP32 side is implemented;
the Pi Zero side is **deferred** pending hands-on hardware testing (see "Pi Zero:
deferred changes"). This doc is the contract both host bindings converge on._

## Goal

The shared `seedsigner` business logic must drive the LVGL screens with **zero
platform-branching boilerplate**. One import, one set of calls, identical source on
Pi Zero (CPython `.so`) and ESP32-S3/P4 (MicroPython C module):

```python
import seedsigner_lvgl

seedsigner_lvgl.init()                  # full board-default bring-up
seedsigner_lvgl.load_locale("th")       # i18n font packs (None/omit base dir)
seedsigner_lvgl.unload_locale()
seedsigner_lvgl.button_list_screen(cfg) # + main_menu_screen / screensaver_screen /
                                        #   seed_add_passphrase_screen / demo_screen
seedsigner_lvgl.poll_for_result()       # → (kind, index, label) | None
seedsigner_lvgl.clear_result_queue()
```

All hardware-specific knobs (resolution, panel pins, color order, SD path) are
**internal per-platform defaults**, overridable via kwargs only for non-standard
hardware. The app passes none of them.

## The unified surface

| Call | Semantics (identical on both platforms) |
|---|---|
| `init()` | Bring up the full LVGL runtime + display + input using this board's defaults. Idempotent. |
| `load_locale(locale, base_dir=None)` | Load the locale's font packs from `<base_dir>/<locale>/`. Returns `True` on full success, `False` if a pack is missing/unreadable (loader falls back to the baked Western/English floor). `base_dir` defaults per platform. |
| `unload_locale()` | Clear loaded packs, restore the baked Western floor. |
| `button_list_screen(cfg)` etc. | Build the screen (returns promptly); results arrive via the queue. |
| `poll_for_result()` / `clear_result_queue()` | Non-blocking result-queue access. |

### Naming decisions

- **`load_locale` / `unload_locale`** (not `set_locale`): a matched load/unload verb
  pair. Supersedes the Pi side's shipped `set_locale`.
- **`seedsigner_lvgl`** is the single public module name on both platforms. On Pi it
  is a Python package wrapping the `seedsigner_lvgl_native` C extension; on ESP32 it
  is the MicroPython C module directly. The underlying C name differing is an
  implementation detail the business logic never sees.

## Why a single `init()` — and why no hardware/LVGL split on the MCU

On the **Pi Zero**, LVGL is driven by a software flush callback decoupled from the
SPI panel driver, so runtime-init (`lvgl_init`) and panel-init
(`native_display_init`) are genuinely separable steps.

On the **ESP32**, `esp_lvgl_port` *fuses* them: LVGL's display is created from the
MIPI-DSI panel handle and its input device from the touch handle — you cannot stand
up the LVGL runtime without the hardware handles in the same breath. So a
"hardware vs LVGL init" split is **meaningless on the microcontroller**.

Resolution: expose **one** `init()`. Internally, ESP32 does it as a single fused
board+LVGL step; Pi does it as two steps wrapped behind the same call. Resolution
stays platform-internal — each board inits at its native size and the shared screen
code adapts via the display-profile / `PX_MULTIPLIER` system, so the app never
passes a resolution.

## End state: one display mode, no mode kwarg

Today the Pi has **two** display modes: the `.so` drives the ST7789 over SPI
(`native_display_init`), *or* SeedSigner's existing Python ST7789 driver owns the
panel and the `.so` only feeds it a flush callback (`set_flush_callback` /
`set_flush_mode("python")` / `native_input_init`).

The plan is to **completely replace the PIL/Python screens with LVGL screens before
the next Pi Zero release**. Once there are no Python screens, the Python ST7789
driver (and the external-display / flush-callback path) is retired, leaving the
native `.so`-driven mode as the **sole** Pi display mode — matching the ESP32's
single board-owned mode. Then:

- The unified `init()` needs **no mode kwarg** in either platform.
- The Pi façade sheds `set_flush_callback`, `bind_display`, `set_flush_mode`, and
  `native_input_init` from its public surface; `init()` = `lvgl_init` +
  `native_display_init` fused, true parity with ESP32.

## Platform implementations

### ESP32-S3 / P4 — implemented (seedsigner-micropython-builder)

- Single MicroPython module **`seedsigner_lvgl`** (`bindings/modseedsigner_bindings.c`)
  exposes `init` + `load_locale` + `unload_locale` + the screens. The separate
  `display_manager` MicroPython module was **retired** — its C impl
  (`ports/esp32/display_manager/display_manager.cpp`) remains as the internal
  platform-runtime layer (board bring-up, `run_screen` LVGL-port locking, the SD
  pack provider, the locale loader), just no longer a Python-facing module.
- `init()` → the component's `init()` (board_init + `set_display`). Hardware is also
  initialized at C boot (`seedsigner_board_startup`), so the Python `init()` is a
  cheap idempotent re-entry that preserves Pi parity.
- **SD card via MicroPython's own FAT VFS, not ESP-IDF's.** ESP-IDF's
  `esp_vfs_fat`/`fatfs` cannot be linked alongside MicroPython's bundled `oofatfs`
  (duplicate `f_mount`/`f_open`/…) — see
  `seedsigner-micropython-builder/docs/knowledge/micropython-fatfs-vs-esp-idf-fatfs-collision.md`.
  So the card is mounted + read on the **MicroPython side** (`machine.SDCard` + FAT
  VFS), and the pack *bytes* are handed to the C loader:
  - `seedsigner_lvgl.locale_pack_files(locale)` → the files this locale needs.
  - Python reads each off the SD card, stages them in a `{filename: bytes}` dict.
  - `seedsigner_lvgl.load_locale(locale, packs)` drives `ss_load_locale` (wrapped in
    the `esp_lvgl_port` lock, since tiny_ttf rasterization mutates LVGL state on the
    LVGL task) through a provider that serves bytes from the dict.

  This is the `locale_loader` "pre-fetch / staging" pattern (the same one the WASM
  playground uses). A thin frozen Python `seedsigner_lvgl` wrapper will later hide
  the SD read so the shared app calls a bare `load_locale(locale)` — matching Pi.

### Pi Zero — deferred changes (seedsigner-raspi-lvgl)

Documented now, implemented later (needs careful on-device verification):

1. Rename `set_locale` → `load_locale` (façade `__init__.py`, `module.cpp` method
   table + qstr-equivalent, `tests/pi_i18n_locale_test.py`, smoke tests).
2. Fuse `lvgl_init` + `native_display_init` into a single `init()` with the
   SeedSigner Pi Zero panel defaults (resolution, ST7789 pins, BGR) baked in.
3. Once PIL screens are fully replaced: retire the external-display / flush-callback
   path and the Python ST7789 driver; drop the corresponding façade functions.

## Trust seam (unchanged)

The host-supplied `ss_pack_provider_t` is where pack-signature verification lives on
the signing device — the render layer never opens files or verifies signatures. See
`seedsigner-lvgl-screens/components/seedsigner/locale_loader.h` and
[`secure-boot-and-signing-keys.md`](secure-boot-and-signing-keys.md).

## Companion docs

- [`dual-platform-overview.md`](dual-platform-overview.md) — the Four-Verbs model and
  layer contracts this API sits inside.
- `seedsigner-raspi-lvgl/docs/architecture.md` — Pi binding surface (notes the
  deferred unification).
