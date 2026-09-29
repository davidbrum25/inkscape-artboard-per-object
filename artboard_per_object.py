#!/usr/bin/env python3
# Copyright (C) 2026 David Brum
# SPDX-License-Identifier: GPL-2.0-or-later
"""Create one Inkscape page for each selected object.

Pages sit on the objects where they already are. The page rectangle is the
object's visual bounding box (stroke included), plus an optional margin.
"""

import math
import re
from tempfile import TemporaryDirectory

import inkex
from inkex.command import inkscape, write_svg
from inkex.localization import inkex_gettext as _


_NUMBER = re.compile(r"^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$")


class ArtboardPerObject(inkex.EffectExtension):
    """One page per selected object."""

    def add_arguments(self, pars):
        pars.add_argument("--margin", type=float, default=0.0)
        pars.add_argument("--margin_unit", default="mm")
        pars.add_argument("--order", default="document")
        pars.add_argument("--label", default="name")
        pars.add_argument("--existing", default="append")
        pars.add_argument("--delete_objects", type=inkex.Boolean, default=False)

    def effect(self):
        elements = [node for node in self.svg.selection if node is not self.svg]
        if not elements:
            raise inkex.AbortExtension(_("Select one or more objects first."))

        for node in elements:
            node.get_id()

        queried = self._query_visual_boxes()
        margin = self._margin_user_units()
        items = []
        skipped = []
        for node in elements:
            box = queried.get(node.get_id())
            if box is None or not box:
                box = self._geometric_box(node)
            if box is None or not box or box.width <= 0 or box.height <= 0:
                skipped.append(node.get_id())
                continue
            box = box.resize(margin)
            if box.width <= 0 or box.height <= 0:
                skipped.append(node.get_id())
                continue
            items.append((node, box))

        if not items:
            raise inkex.AbortExtension(
                _("None of the selected objects have a bounding box that can become a page.")
            )

        items = self._sort_items(items)
        self._add_pages(items)

        if self.options.delete_objects:
            for node, _box in items:
                if node.getparent() is not None:
                    node.delete()

        if skipped:
            inkex.errormsg(
                _("Skipped {count} object(s) with no usable bounding box: {ids}").format(
                    count=len(skipped), ids=", ".join(skipped)
                )
            )

    def _margin_user_units(self):
        amount = self.options.margin
        unit = self.options.margin_unit
        if not amount or unit == "uu":
            return amount
        return self.svg.viewport_to_unit(f"{amount}{unit}")

    def _query_visual_boxes(self):
        """Visual bounding boxes in SVG user units, keyed by element id."""
        try:
            with TemporaryDirectory(prefix="artboard-per-object-") as tmpdir:
                svg_file = write_svg(self.svg, tmpdir, "input.svg")
                output = inkscape(svg_file, "--query-all")
        except (OSError, ValueError) as error:
            inkex.errormsg(
                _(
                    "Could not ask Inkscape for visual bounding boxes ({error}). "
                    "Pages use geometric boxes instead, so text and stroke may be tight."
                ).format(error=error)
            )
            return {}

        scale = self.svg.equivalent_transform_scale or 1.0
        viewbox = self.svg.get_viewbox()
        origin_x = viewbox[0] if viewbox else 0.0
        origin_y = viewbox[1] if viewbox else 0.0
        boxes = {}
        for line in output.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) != 5 or not _NUMBER.match(parts[1]):
                continue
            element_id, x_px, y_px, w_px, h_px = parts
            x_px, y_px, w_px, h_px = (float(value) for value in (x_px, y_px, w_px, h_px))
            boxes[element_id] = inkex.BoundingBox.new_xywh(
                origin_x + x_px / scale,
                origin_y + y_px / scale,
                w_px / scale,
                h_px / scale,
            )
        return boxes

    def _geometric_box(self, node):
        parent = node.getparent()
        extra = parent.composed_transform() if parent is not None else None
        try:
            return node.bounding_box(extra)
        except (AttributeError, ValueError, TypeError):
            return None

    def _sort_items(self, items):
        order = self.options.order
        if order == "rows":
            return _cluster_sort(items, axis="y")
        if order == "columns":
            return _cluster_sort(items, axis="x")
        return items

    def _add_pages(self, items):
        namedview = self.svg.namedview
        if self.options.existing == "replace":
            for page in list(namedview._get_pages()):
                page.delete()

        # A single-page file often stores that page only as the viewBox.
        # Keep it as a real page element when appending.
        if self.options.existing != "replace" and not namedview._get_pages():
            self._add_page(namedview, self._document_page_box(), None)

        used_labels = {
            page.get("inkscape:label")
            for page in namedview._get_pages()
            if page.get("inkscape:label")
        }
        number_start = len(namedview._get_pages()) + 1
        for index, (node, box) in enumerate(items, start=number_start):
            self._add_page(namedview, box, self._page_label(node, index, used_labels))

    def _document_page_box(self):
        viewbox = self.svg.get_viewbox()
        if viewbox and viewbox[2] and viewbox[3]:
            return inkex.BoundingBox.new_xywh(viewbox[0], viewbox[1], viewbox[2], viewbox[3])
        return inkex.BoundingBox.new_xywh(0, 0, self.svg.viewbox_width, self.svg.viewbox_height)

    def _add_page(self, namedview, box, label):
        # Page.new stringifies values. The Page(width=float) constructor fails
        # under lxml on Python 3.14, which is what namedview.new_page() uses.
        page = inkex.Page.new(
            _format_number(box.width),
            _format_number(box.height),
            _format_number(box.x.minimum),
            _format_number(box.y.minimum),
        )
        namedview.add(page)
        page.set_id(self.svg.get_unique_id("page"))
        if label:
            page.set("inkscape:label", label)
        return page

    def _page_label(self, node, number, used_labels):
        mode = self.options.label
        if mode == "number":
            base = str(number)
        elif mode == "id":
            base = node.get_id()
        else:
            base = node.get("inkscape:label") or node.get_id()
        base = str(base).strip() or _("Page")
        label = base
        suffix = 2
        while label in used_labels:
            label = f"{base} {suffix}"
            suffix += 1
        used_labels.add(label)
        return label


def _format_number(value):
    """Shorten user-unit coordinates. Query results are CSS pixels, so a
    round millimetre often comes back as 100.000064."""
    if not math.isfinite(value):
        return "0"
    rounded = round(float(value), 4)
    coarser = round(rounded, 3)
    if abs(rounded - coarser) < 5e-4:
        rounded = coarser
    text = f"{rounded:.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _cluster_sort(items, axis):
    """Group objects that share a row or column, then order along that band."""
    primary = "y" if axis == "y" else "x"
    secondary = "x" if axis == "y" else "y"

    def position(item, name):
        box = item[1]
        interval = box.y if name == "y" else box.x
        return interval.minimum

    def size(item, name):
        box = item[1]
        return box.height if name == "y" else box.width

    ordered = sorted(items, key=lambda item: (position(item, primary), position(item, secondary)))
    bands = []
    for item in ordered:
        placed = False
        for band in bands:
            anchor = band[0]
            tolerance = 0.5 * max(size(item, primary), size(anchor, primary), 1e-6)
            if abs(position(item, primary) - position(anchor, primary)) <= tolerance:
                band.append(item)
                placed = True
                break
        if not placed:
            bands.append([item])
    result = []
    for band in bands:
        band.sort(key=lambda item: position(item, secondary))
        result.extend(band)
    return result


if __name__ == "__main__":
    ArtboardPerObject().run()
