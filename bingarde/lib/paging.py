"""Paging for the build list: which rows to fetch and which page links to show.

``links()`` lists page numbers with ``None`` for a gap. Page 7 of 21:

    [1, None, 5, 6, 7, 8, 9, None, 21]

The first and last pages are always there, plus ``radius`` pages on each side
of the current one. A gap that would hide a single page shows that page.
"""


class Paging(object):

    def __init__(self, total, per_page, page=1):
        self.total = total
        self.per_page = per_page
        self.pages = max(1, -(-total // per_page))
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1
        self.page = min(max(page, 1), self.pages)

    @property
    def offset(self):
        return (self.page - 1) * self.per_page

    @property
    def first_row(self):
        """1-based number of the first row on this page (0 when there are none)."""
        return self.offset + 1 if self.total else 0

    @property
    def last_row(self):
        return min(self.offset + self.per_page, self.total)

    @property
    def prev(self):
        return self.page - 1 if self.page > 1 else None

    @property
    def next(self):
        return self.page + 1 if self.page < self.pages else None

    def links(self, radius=2):
        near = range(self.page - radius, self.page + radius + 1)
        shown = sorted({1, self.pages, *(p for p in near if 1 <= p <= self.pages)})
        out, last = [], None
        for p in shown:
            if last is not None and p - last == 2:
                out.append(last + 1)
            elif last is not None and p - last > 2:
                out.append(None)
            out.append(p)
            last = p
        return out
