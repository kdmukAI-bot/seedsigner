def mark_for_translation(message: str) -> str:
    # Wraps the target string literal for translation but does NOT return the translated string.
    return message


def scan_instructions_line(hint: str) -> str:
    """Build the hardware-mode scan overlay instruction line: the back affordance plus the
    localized scan hint, e.g. "< back  |  Scan a QR code".

    Used by the native LVGL scan path (gui/lvgl_screen_runner.run_camera_scan, via the
    ScanViews) to render the instruction line. ``hint`` is the bare, translation-marked hint (e.g.
    ScanView.instructions_text); both it and "back" are translated here.
    """
    from seedsigner.compat.l10n import gettext as _
    return "< " + _("back") + "  |  " + _(hint)