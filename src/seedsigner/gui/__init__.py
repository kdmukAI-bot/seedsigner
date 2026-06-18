# Intentionally empty. Submodules (renderer, components, screens, constants, ...) are
# imported directly by their callers, so importing this package does not pull in the
# PIL-backed Renderer. That keeps PIL-free modules (e.g. gui.constants) importable from
# the MicroPython-targeted business logic without dragging in the rendering stack.
