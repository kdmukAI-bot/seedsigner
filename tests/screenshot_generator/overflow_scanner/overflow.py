"""
Translation overflow detection scanner.

Scans fully-constructed Screen instances for layout overflow caused by
translated text. Detects two types of issues:
  1. Bounds overflow: component extends past the 240px canvas bottom
  2. Body-vs-button collision: body content overlaps bottom-anchored buttons

All detection is post-construction inspection of component positions — zero
production code changes.
"""
import os
import pathlib
import re
from dataclasses import dataclass, field
from PIL import Image, ImageDraw, ImageFont


# SettingsEntryUpdateSelectionScreen already overlaps in English when there are
# many options. Only flag when overflow exceeds this threshold.
KNOWN_OVERLAP_PREFIXES = {
    "SettingsEntryUpdateSelectionView_": 22,  # px; pre-existing EN overlap
}


@dataclass
class OverflowEvent:
    event_type: str          # "bounds" or "collision"
    screen_name: str
    locale: str
    component_text: str      # rendered text on the component
    overflow_px: int         # pixels past the boundary
    component_type: str      # class name, e.g. "TextArea"
    screen_y: int            # component's Y position
    effective_height: int    # computed actual height
    msgid: str = ""          # English source string (filled in post-scan)
    msgstr: str = ""         # translated string (filled in post-scan)


def get_true_text_height(textarea) -> int:
    """
    Recalculate actual rendered text height from TextArea instance attributes.
    Mirrors the logic at components.py:444-455. For fixed-height TextAreas,
    .height is the allocated box; the actual text may render past it.
    """
    n = len(textarea.text_lines)
    if n == 1:
        h = textarea.text_height_above_baseline
        if not textarea.height_ignores_below_baseline:
            h += textarea.text_height_below_baseline
    else:
        h = (textarea.text_height_above_baseline * n
             + textarea.line_spacing * (n - 1))
        if not textarea.height_ignores_below_baseline:
            h += textarea.text_lines[-1].get("px_below_baseline", 0)
    return h


def get_effective_height(component) -> int:
    """Return the actual rendered height for a component."""
    from seedsigner.gui.components import TextArea
    if isinstance(component, TextArea):
        return max(component.height or 0, get_true_text_height(component))
    return component.height or 0


def _get_component_text(component) -> str:
    """Extract display text from a component, if it has one."""
    return getattr(component, 'text', '') or ''


def scan_for_overflow(screen, screen_name: str, locale: str,
                      canvas_height: int = 240) -> list:
    """
    Scan all visual elements for bounds violations and body-vs-button collisions.
    Returns a list of OverflowEvent instances.
    """
    from seedsigner.gui.components import TextArea
    events = []

    has_buttons = hasattr(screen, 'buttons') and screen.buttons
    has_scroll = getattr(screen, 'has_scroll_arrows', False)
    is_bottom_list = getattr(screen, 'is_bottom_list', False)

    # Determine known overlap threshold for this screen
    acceptable_overlap = 0
    for prefix, threshold in KNOWN_OVERLAP_PREFIXES.items():
        if screen_name.startswith(prefix):
            acceptable_overlap = threshold
            break

    # Check 1: Bounds overflow on components
    for comp in screen.components:
        comp_y = getattr(comp, 'screen_y', None)
        if comp_y is None:
            continue
        eff_h = get_effective_height(comp)
        bottom = comp_y + eff_h
        if bottom > canvas_height:
            overflow = bottom - canvas_height
            events.append(OverflowEvent(
                event_type="bounds",
                screen_name=screen_name,
                locale=locale,
                component_text=_get_component_text(comp),
                overflow_px=overflow,
                component_type=type(comp).__name__,
                screen_y=comp_y,
                effective_height=eff_h,
            ))

    # Check bounds overflow on buttons (only when not scrollable)
    if has_buttons and not has_scroll:
        for btn in screen.buttons:
            bottom = btn.screen_y + (btn.height or 0)
            if bottom > canvas_height:
                events.append(OverflowEvent(
                    event_type="bounds",
                    screen_name=screen_name,
                    locale=locale,
                    component_text=_get_component_text(btn),
                    overflow_px=bottom - canvas_height,
                    component_type=type(btn).__name__,
                    screen_y=btn.screen_y,
                    effective_height=btn.height or 0,
                ))

    # Check 2: Body-vs-button collision
    if has_buttons and is_bottom_list and not has_scroll:
        # Find the top of the button area (accounting for scroll offset)
        button_top_y = min(
            btn.screen_y - getattr(btn, 'scroll_y', 0)
            for btn in screen.buttons
        )

        button_set = set(id(btn) for btn in screen.buttons)
        for comp in screen.components:
            if id(comp) in button_set:
                continue
            comp_y = getattr(comp, 'screen_y', None)
            if comp_y is None:
                continue
            eff_h = get_effective_height(comp)
            comp_bottom = comp_y + eff_h
            if comp_bottom > button_top_y:
                overlap = comp_bottom - button_top_y
                if overlap > acceptable_overlap:
                    events.append(OverflowEvent(
                        event_type="collision",
                        screen_name=screen_name,
                        locale=locale,
                        component_text=_get_component_text(comp),
                        overflow_px=overlap,
                        component_type=type(comp).__name__,
                        screen_y=comp_y,
                        effective_height=eff_h,
                    ))

    # Check paste_images for bounds overflow
    for img, coords in screen.paste_images:
        x, y = coords
        bottom = y + img.height
        if bottom > canvas_height:
            events.append(OverflowEvent(
                event_type="bounds",
                screen_name=screen_name,
                locale=locale,
                component_text="",
                overflow_px=bottom - canvas_height,
                component_type="paste_image",
                screen_y=y,
                effective_height=img.height,
            ))

    return events


# --- .po file parser and msgid reverse lookup ---

_po_cache: dict[str, dict[str, str]] = {}


def _parse_po_file(locale: str) -> dict[str, str]:
    """
    Parse a .po file and return a msgstr -> msgid mapping.
    Cached per locale.
    """
    if locale in _po_cache:
        return _po_cache[locale]

    po_path = os.path.join(
        pathlib.Path(__file__).parent.parent.parent.resolve(),
        "src", "seedsigner", "resources", "seedsigner-translations",
        "l10n", locale, "LC_MESSAGES", "messages.po"
    )

    mapping = {}
    if not os.path.exists(po_path):
        _po_cache[locale] = mapping
        return mapping

    with open(po_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].rstrip('\n')

        # Look for msgid lines
        if line.startswith('msgid "'):
            msgid_str = _extract_quoted_string(line[6:])
            i += 1
            # Collect continuation lines
            while i < len(lines) and lines[i].rstrip('\n').startswith('"'):
                msgid_str += _extract_quoted_string(lines[i].rstrip('\n'))
                i += 1

            # Now expect msgstr
            if i < len(lines) and lines[i].rstrip('\n').startswith('msgstr "'):
                msgstr_str = _extract_quoted_string(lines[i].rstrip('\n')[7:])
                i += 1
                while i < len(lines) and lines[i].rstrip('\n').startswith('"'):
                    msgstr_str += _extract_quoted_string(lines[i].rstrip('\n'))
                    i += 1

                # Skip empty translations and the header entry
                if msgid_str and msgstr_str:
                    # Unescape for matching: \n -> newline, %% -> %
                    msgid_clean = msgid_str.replace('\\n', '\n').replace('%%', '%')
                    msgstr_clean = msgstr_str.replace('\\n', '\n').replace('%%', '%')
                    mapping[msgstr_clean] = msgid_clean
            # Handle plural forms (msgid_plural / msgstr[N])
            elif i < len(lines) and lines[i].rstrip('\n').startswith('msgid_plural'):
                # Skip plural entries for now — they're less common in overflow contexts
                while i < len(lines) and not lines[i].rstrip('\n') == '':
                    i += 1
        else:
            i += 1

    _po_cache[locale] = mapping
    return mapping


def _extract_quoted_string(s: str) -> str:
    """Extract the content between the first pair of double quotes."""
    start = s.find('"')
    if start == -1:
        return ""
    end = s.rfind('"')
    if end <= start:
        return ""
    return s[start + 1:end]


def reverse_lookup_msgid(text: str, locale: str) -> tuple:
    """
    Find the English msgid for a translated string.
    Returns (msgid, msgstr). Falls back to regex matching for format strings.
    """
    if not text or locale == "en":
        return (text, text)

    mapping = _parse_po_file(locale)

    # Direct match
    if text in mapping:
        return (mapping[text], text)

    # Regex fallback for format strings: try each msgstr that contains {}
    for msgstr, msgid in mapping.items():
        if '{}' not in msgstr and '{' not in msgstr:
            continue
        # Build a pattern from the msgstr: replace {} with .+ and escape the rest
        pattern_parts = re.split(r'\{[^}]*\}', msgstr)
        pattern = '.+'.join(re.escape(p) for p in pattern_parts)
        try:
            if re.fullmatch(pattern, text):
                return (msgid, msgstr)
        except re.error:
            continue

    return ("", text)


# --- Composite image generation ---

def _get_annotation_font(size: int, locale: str) -> ImageFont.FreeTypeFont:
    """Load a font that can render the locale's glyphs for composite annotations."""
    from seedsigner.gui.components import GUIConstants, Fonts
    font_name = GUIConstants.BASE_LOCALE_FONTS.get(locale, "OpenSans-Regular")
    for font_dir in Fonts.font_paths:
        path = os.path.join(font_dir, f"{font_name}.ttf")
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_composite_image(en_screenshot_path: str,
                             translated_screenshot_path: str,
                             events: list,
                             screen_name: str) -> Image.Image:
    """
    Create a side-by-side composite image with EN on the left and translated
    on the right. Draws red outlines on the translated side at overflow
    locations. Adds header and annotation text.
    """
    SCREEN_SIZE = 240
    PADDING = 16
    HEADER_HEIGHT = 60
    TITLE_FONT_SIZE = 20
    ANNOTATION_FONT_SIZE = 18
    ANNOTATION_LINE_HEIGHT = ANNOTATION_FONT_SIZE + 6
    ANNOTATION_FIELD_SPACING = ANNOTATION_LINE_HEIGHT + 8
    ANNOTATION_COLOR = (192, 192, 192)

    # Load screenshots
    try:
        en_img = Image.open(en_screenshot_path).convert('RGB')
    except (FileNotFoundError, OSError):
        en_img = Image.new('RGB', (SCREEN_SIZE, SCREEN_SIZE), color=(40, 40, 40))
        draw = ImageDraw.Draw(en_img)
        draw.text((10, 110), "EN screenshot\nnot available", fill=(128, 128, 128))

    translated_img = Image.open(translated_screenshot_path).convert('RGB')

    # Draw debug outlines on a copy of the translated screenshot
    annotated = translated_img.copy()
    draw = ImageDraw.Draw(annotated)
    for event in events:
        y_top = max(0, event.screen_y)
        y_bottom = min(event.screen_y + event.effective_height, SCREEN_SIZE - 1)
        if y_bottom <= y_top:
            continue
        color = "red" if event.event_type == "bounds" else "yellow"
        draw.rectangle(
            [(0, y_top), (SCREEN_SIZE - 1, y_bottom)],
            outline=color, width=1
        )

    # Pick fonts that can render the locale's glyphs
    locale = events[0].locale if events else "en"
    title_font = _get_annotation_font(TITLE_FONT_SIZE, locale)
    annotation_font = _get_annotation_font(ANNOTATION_FONT_SIZE, locale)

    # Calculate composite dimensions
    total_width = PADDING + SCREEN_SIZE + PADDING + SCREEN_SIZE + PADDING
    usable_text_width = total_width - 2 * PADDING

    # Build annotation lines. Each entry is (text, is_field_start, label_prefix).
    # label_prefix (if set) renders in white; the rest renders in gray.
    LABEL_COLOR = (230, 180, 80)
    annotation_lines = []

    def _add_wrapped(text, font, width, is_first, label_prefix=""):
        """Wrap text and append to annotation_lines. The label_prefix on the
        first wrapped line renders in a different color."""
        if label_prefix:
            # Measure label width so wrapped content accounts for it
            label_w = font.getbbox(label_prefix)[2] - font.getbbox(label_prefix)[0]
            content_width = width - label_w
            wrapped = _wrap_text(text, font, content_width)
            annotation_lines.append((wrapped[0], is_first, label_prefix))
            for cont in wrapped[1:]:
                annotation_lines.append((cont, False, ""))
        else:
            wrapped = _wrap_text(text, font, width)
            annotation_lines.append((wrapped[0], is_first, ""))
            for cont in wrapped[1:]:
                annotation_lines.append((cont, False, ""))

    for event in events:
        _add_wrapped(
            f"[{event.event_type.upper()}] {event.component_type}: overflow {event.overflow_px}px",
            annotation_font, usable_text_width, is_first=True)
        if event.msgid:
            _add_wrapped(
                event.msgid,
                annotation_font, usable_text_width, is_first=True, label_prefix="msgid: ")
        if event.msgstr:
            _add_wrapped(
                event.msgstr,
                annotation_font, usable_text_width, is_first=True, label_prefix="msgstr: ")

    # Calculate total annotation height
    annotation_height = PADDING
    for i, (_, is_field_start, _lbl) in enumerate(annotation_lines):
        annotation_height += ANNOTATION_FIELD_SPACING if (is_field_start and i > 0) else ANNOTATION_LINE_HEIGHT
    annotation_height += PADDING

    total_height = HEADER_HEIGHT + SCREEN_SIZE + annotation_height

    composite = Image.new('RGB', (total_width, total_height), color=(0, 0, 0))
    comp_draw = ImageDraw.Draw(composite)

    # Header — centered
    title_bbox = title_font.getbbox(screen_name)
    title_w = title_bbox[2] - title_bbox[0]
    title_x = (total_width - title_w) // 2
    comp_draw.text((title_x, PADDING), screen_name, fill=(255, 255, 255), font=title_font)

    # Paste screenshots with border outlines
    en_x, en_y = PADDING, HEADER_HEIGHT
    tr_x, tr_y = PADDING + SCREEN_SIZE + PADDING, HEADER_HEIGHT
    composite.paste(en_img, (en_x, en_y))
    composite.paste(annotated, (tr_x, tr_y))
    border_color = (80, 80, 80)
    comp_draw.rectangle([(en_x - 1, en_y - 1), (en_x + SCREEN_SIZE, en_y + SCREEN_SIZE)], outline=border_color)
    comp_draw.rectangle([(tr_x - 1, tr_y - 1), (tr_x + SCREEN_SIZE, tr_y + SCREEN_SIZE)], outline=border_color)

    # Annotations
    y = HEADER_HEIGHT + SCREEN_SIZE + PADDING
    for i, (text, is_field_start, label_prefix) in enumerate(annotation_lines):
        if is_field_start and i > 0:
            y += ANNOTATION_FIELD_SPACING - ANNOTATION_LINE_HEIGHT
        x = PADDING
        if label_prefix:
            comp_draw.text((x, y), label_prefix, fill=LABEL_COLOR, font=annotation_font)
            label_bbox = annotation_font.getbbox(label_prefix)
            x += label_bbox[2] - label_bbox[0]
        comp_draw.text((x, y), text, fill=ANNOTATION_COLOR, font=annotation_font)
        y += ANNOTATION_LINE_HEIGHT

    return composite


def _wrap_text(text: str, font, max_width: int) -> list:
    """Word-wrap text to fit within max_width pixels. Splits on newlines first,
    then wraps each line on word boundaries. Falls back to character-level
    breaking for words that exceed max_width (common with CJK)."""
    result = []
    for paragraph in text.split('\n'):
        if not paragraph:
            result.append("")
            continue
        result.extend(_wrap_line(paragraph, font, max_width))
    return result or [""]


def _wrap_line(text: str, font, max_width: int) -> list:
    """Wrap a single line of text (no embedded newlines)."""
    words = text.split(' ')
    lines = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip() if current else word
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
            # Check if this single word fits; if not, break it by character
            bbox = font.getbbox(word)
            if bbox[2] - bbox[0] <= max_width:
                current = word
            else:
                current = ""
                for char in word:
                    test = current + char
                    bbox = font.getbbox(test)
                    if bbox[2] - bbox[0] <= max_width:
                        current = test
                    else:
                        if current:
                            lines.append(current)
                        current = char
    if current:
        lines.append(current)
    return lines or [""]


def write_summary(events: list, output_dir: str, locale: str):
    """Write a summary.txt with one line per overflow event."""
    os.makedirs(output_dir, exist_ok=True)
    summary_path = os.path.join(output_dir, "summary.txt")

    with open(summary_path, 'w', encoding='utf-8') as f:
        f.write(f"Overflow Report for locale: {locale}\n")
        f.write(f"Total issues: {len(events)}\n")
        f.write("-" * 80 + "\n\n")

        for event in sorted(events, key=lambda e: -e.overflow_px):
            msgid_str = f' | msgid: {event.msgid}' if event.msgid else ''
            msgstr_str = f' | msgstr: {event.msgstr}' if event.msgstr else ''
            f.write(
                f"[{event.event_type.upper():9s}] {event.screen_name} | "
                f"{event.component_type} | overflow: {event.overflow_px}px"
                f"{msgid_str}{msgstr_str}\n"
            )

    return summary_path
