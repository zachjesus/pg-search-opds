from __future__ import annotations

from typing import Any, Callable, Dict, List
from urllib.parse import quote, unquote, urlencode

import cherrypy

from search.constants import (
    Crosswalk,
    CuratedBookshelves,
    Language,
    LoCCMainClass,
    OrderBy,
    SearchType,
    SortDirection,
)
from search.full_text_search import FullTextSearch

SAMPLE_LIMIT = 15
LANGUAGE_LIST = [{"code": l.code, "label": l.label} for l in Language]
_VALID_SORTS = set(OrderBy._value2member_map_.keys())


def _parse_search_type(field: str) -> SearchType:
    """Parse field param to SearchType. Field name is ignored."""
    if field.startswith("fts_") or field == "fts":
        return SearchType.FTS
    if field.startswith("fuzzy_") or field == "fuzzy":
        return SearchType.FUZZY
    return SearchType.FUZZY


def _facet_link(href: str, title: str, is_active: bool) -> dict:
    """Build a facet link. Only includes 'rel' if active (per OPDS 2.0 spec)."""
    link = {"href": href, "type": "application/opds+json", "title": title}
    if is_active:
        link["rel"] = "self"
    return link


def _url_with_params(path: str, params: dict) -> str:
    """Build URL with proper query-string encoding."""
    clean = {k: v for k, v in params.items() if v not in ("", None)}
    qs = urlencode(clean, doseq=True)
    return f"{path}?{qs}" if qs else path


def _parse_pagination(page, limit, default_limit=28):
    """Parse and clamp pagination params."""
    try:
        return max(1, int(page)), max(1, min(100, int(limit)))
    except (ValueError, TypeError):
        return 1, default_limit


class API:
    def __init__(self):
        self.fts = FullTextSearch()

    # ========== Common Helpers ==========

    def _apply_filters(
        self, q, query: str, lang: str, copyrighted: str, audiobook: str
    ):
        """Apply common filters to a query object."""
        if query.strip():
            q.search(query, search_type=_parse_search_type("keyword"))
        if lang:
            q.lang(lang)
        if copyrighted == "true":
            q.copyrighted()
        elif copyrighted == "false":
            q.public_domain()
        if audiobook == "true":
            q.audiobook()
        elif audiobook == "false":
            q.text_only()
        return q

    def _apply_sort(self, q, sort: str, sort_order: str, has_query: bool):
        """Apply sorting to a query object."""
        if sort in _VALID_SORTS:
            direction = (
                SortDirection.ASC
                if sort_order == "asc"
                else SortDirection.DESC
                if sort_order == "desc"
                else None
            )
            q.order_by(OrderBy(sort), direction)
        elif has_query:
            q.order_by(OrderBy.RELEVANCE)
        else:
            q.order_by(OrderBy.DOWNLOADS)
        return q

    def _append_pagination_links(
        self, links: List[Dict[str, Any]], build_url_fn: Callable, result: dict
    ):
        """Append first/previous/next/last pagination links to links list."""
        page, total_pages = result.get("page", 1), result.get("total_pages", 1)
        if page > 1:
            links.extend(
                [
                    {
                        "rel": "first",
                        "href": build_url_fn(1),
                        "type": "application/opds+json",
                    },
                    {
                        "rel": "previous",
                        "href": build_url_fn(page - 1),
                        "type": "application/opds+json",
                    },
                ]
            )
        if page < total_pages:
            links.extend(
                [
                    {
                        "rel": "next",
                        "href": build_url_fn(page + 1),
                        "type": "application/opds+json",
                    },
                    {
                        "rel": "last",
                        "href": build_url_fn(total_pages),
                        "type": "application/opds+json",
                    },
                ]
            )

    def _build_common_facets(
        self,
        url_fn,
        query,
        lang,
        copyrighted,
        audiobook,
        sort,
        sort_order,
        top_subjects=None,
    ):
        """Build common facets (sort, copyright, format, language, optional subjects)."""
        facets = [
            {
                "metadata": {"title": "Sort By"},
                "links": [
                    _facet_link(
                        url_fn(
                            query, lang, copyrighted, audiobook, "downloads", "desc"
                        ),
                        "Most Popular",
                        sort == "downloads" or not sort,
                    ),
                    _facet_link(
                        url_fn(query, lang, copyrighted, audiobook, "relevance", ""),
                        "Relevance",
                        sort == "relevance",
                    ),
                    _facet_link(
                        url_fn(query, lang, copyrighted, audiobook, "title", "asc"),
                        "Title (A-Z)",
                        sort == "title",
                    ),
                    _facet_link(
                        url_fn(query, lang, copyrighted, audiobook, "author", "asc"),
                        "Author (A-Z)",
                        sort == "author",
                    ),
                    _facet_link(
                        url_fn(query, lang, copyrighted, audiobook, "random", ""),
                        "Random",
                        sort == "random",
                    ),
                ],
            }
        ]

        if top_subjects:
            facets.append(
                {
                    "metadata": {"title": "Top Subjects in Results"},
                    "links": [
                        {
                            "href": f"/opds/subjects?id={s['id']}",
                            "type": "application/opds+json",
                            "title": f"{s['name']} ({s['count']})",
                        }
                        for s in top_subjects
                    ],
                }
            )

        facets.extend(
            [
                {
                    "metadata": {"title": "Copyright Status"},
                    "links": [
                        _facet_link(
                            url_fn(query, lang, "", audiobook, sort, sort_order),
                            "Any",
                            not copyrighted,
                        ),
                        _facet_link(
                            url_fn(query, lang, "false", audiobook, sort, sort_order),
                            "Public Domain",
                            copyrighted == "false",
                        ),
                        _facet_link(
                            url_fn(query, lang, "true", audiobook, sort, sort_order),
                            "Copyrighted",
                            copyrighted == "true",
                        ),
                    ],
                },
                {
                    "metadata": {"title": "Format"},
                    "links": [
                        _facet_link(
                            url_fn(query, lang, copyrighted, "", sort, sort_order),
                            "Any",
                            not audiobook,
                        ),
                        _facet_link(
                            url_fn(query, lang, copyrighted, "false", sort, sort_order),
                            "Text",
                            audiobook == "false",
                        ),
                        _facet_link(
                            url_fn(query, lang, copyrighted, "true", sort, sort_order),
                            "Audiobook",
                            audiobook == "true",
                        ),
                    ],
                },
                {
                    "metadata": {"title": "Language"},
                    "links": [
                        _facet_link(
                            url_fn(query, "", copyrighted, audiobook, sort, sort_order),
                            "Any",
                            not lang,
                        )
                    ]
                    + [
                        _facet_link(
                            url_fn(
                                query,
                                item["code"],
                                copyrighted,
                                audiobook,
                                sort,
                                sort_order,
                            ),
                            item["label"],
                            lang == item["code"],
                        )
                        for item in LANGUAGE_LIST
                    ],
                },
            ]
        )
        return facets

    def _get_top_subjects(self, base_query_fn, query, lang, copyrighted, audiobook):
        """Fetch top subjects for facets."""
        try:
            q_sub = base_query_fn()
            self._apply_filters(q_sub, query, lang, copyrighted, audiobook)
            return self.fts.get_top_subjects_for_query(q_sub, limit=15, max_books=500)
        except Exception as e:
            cherrypy.log(f"Top subjects error: {e}")
            return None

    # ========== Index ==========

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def index(self):
        """Root catalog - navigation only."""
        return {
            "metadata": {"title": "Project Gutenberg Catalog"},
            "links": [
                {"rel": "self", "href": "/opds/", "type": "application/opds+json"},
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {
                    "rel": "search",
                    "href": "/opds/search{?query}",
                    "type": "application/opds+json",
                    "templated": True,
                },
            ],
            "navigation": [
                {
                    "href": "/opds/search?field=fuzzy",
                    "title": "Search Fuzzy (Typo-Tolerant, Slower)",
                    "type": "application/opds+json",
                    "rel": "subsection",
                },
                {
                    "href": "/opds/search?field=fts",
                    "title": 'Search FTS (Strict, Faster, operators: "quotes", or, and, - for negate)',
                    "type": "application/opds+json",
                    "rel": "subsection",
                },
                {
                    "href": "/opds/bookshelves",
                    "title": "Browse by Bookshelf",
                    "type": "application/opds+json",
                    "rel": "subsection",
                },
                {
                    "href": "/opds/loccs",
                    "title": "Browse by LoCC (Subject Classification)",
                    "type": "application/opds+json",
                    "rel": "subsection",
                },
                {
                    "href": "/opds/subjects",
                    "title": "Browse by Subject",
                    "type": "application/opds+json",
                    "rel": "subsection",
                },
                {
                    "href": "/opds/search?sort=downloads&sort_order=desc",
                    "title": "Most Popular",
                    "type": "application/opds+json",
                    "rel": "http://opds-spec.org/sort/popular",
                },
                {
                    "href": "/opds/search?sort=release_date&sort_order=desc",
                    "title": "Recently Added",
                    "type": "application/opds+json",
                    "rel": "http://opds-spec.org/sort/new",
                },
                {
                    "href": "/opds/search?sort=random",
                    "title": "Random",
                    "type": "application/opds+json",
                    "rel": "http://opds-spec.org/sort/random",
                },
            ],
        }

    # ========== Bookshelves ==========

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def bookshelves(
        self,
        id: int | None = None,
        category: str | None = None,
        page: int = 1,
        limit: int = 28,
        query: str = "",
        lang: str = "",
        copyrighted: str = "",
        audiobook: str = "",
        sort: str = "",
        sort_order: str = "",
    ):
        """Bookshelf navigation using CuratedBookshelves."""
        page, limit = _parse_pagination(page, limit)

        # Detail view for a single bookshelf id
        if id is not None:
            return self._bookshelf_detail(
                int(id),
                page,
                limit,
                query,
                lang,
                copyrighted,
                audiobook,
                sort,
                sort_order,
            )

        # Category listing
        if category is not None:
            return self._bookshelf_category(unquote(category))

        # Root: list all categories
        return {
            "metadata": {
                "title": "Bookshelves",
                "numberOfItems": len(CuratedBookshelves),
            },
            "links": [
                {
                    "rel": "self",
                    "href": "/opds/bookshelves",
                    "type": "application/opds+json",
                },
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {"rel": "up", "href": "/opds/", "type": "application/opds+json"},
            ],
            "navigation": [
                {
                    "href": f"/opds/bookshelves?category={quote(cat.genre)}",
                    "title": f"{cat.genre} ({len(cat.shelves)} shelves)",
                    "type": "application/opds+json",
                    "rel": "subsection",
                }
                for cat in CuratedBookshelves
            ],
        }

    def _bookshelf_detail(
        self,
        bookshelf_id: int,
        page: int,
        limit: int,
        query: str,
        lang: str,
        copyrighted: str,
        audiobook: str,
        sort: str,
        sort_order: str,
    ):
        """Browse books in a specific bookshelf."""
        bookshelf_name, parent_category = f"Bookshelf {bookshelf_id}", None
        for cat in CuratedBookshelves:
            for sid, sname in cat.shelves:
                if sid == bookshelf_id:
                    bookshelf_name, parent_category = sname, cat.genre
                    break
            if parent_category:
                break

        try:
            q = self.fts.query(crosswalk=Crosswalk.OPDS)
            q.bookshelf_id(bookshelf_id)
            self._apply_filters(q, query, lang, copyrighted, audiobook)
            self._apply_sort(q, sort, sort_order, bool(query.strip()))
            q[page, limit]
            result = self.fts.execute(q)
        except Exception as e:
            cherrypy.log(f"Bookshelf browse error: {e}")
            raise cherrypy.HTTPError(500, "Browse failed")

        def build_url(p: int) -> str:
            return _url_with_params(
                "/opds/bookshelves",
                {
                    "id": bookshelf_id,
                    "query": query,
                    "page": p,
                    "limit": limit,
                    "lang": lang,
                    "copyrighted": copyrighted,
                    "audiobook": audiobook,
                    "sort": sort,
                    "sort_order": sort_order,
                },
            )

        def url_fn(q, lng, cr, ab, srt, srt_ord):
            return _url_with_params(
                "/opds/bookshelves",
                {
                    "id": bookshelf_id,
                    "query": q,
                    "page": 1,
                    "limit": limit,
                    "lang": lng,
                    "copyrighted": cr,
                    "audiobook": ab,
                    "sort": srt,
                    "sort_order": srt_ord,
                },
            )

        top_subjects = self._get_top_subjects(
            lambda: self.fts.query().bookshelf_id(bookshelf_id),
            query,
            lang,
            copyrighted,
            audiobook,
        )
        up_href = (
            f"/opds/bookshelves?category={quote(parent_category)}"
            if parent_category
            else "/opds/bookshelves"
        )

        feed = {
            "metadata": {
                "title": bookshelf_name,
                "numberOfItems": result["total"],
                "itemsPerPage": result["page_size"],
                "currentPage": result["page"],
            },
            "links": [
                {
                    "rel": "self",
                    "href": build_url(result["page"]),
                    "type": "application/opds+json",
                },
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {"rel": "up", "href": up_href, "type": "application/opds+json"},
                {
                    "rel": "search",
                    "href": f"/opds/bookshelves?id={bookshelf_id}{{&query}}",
                    "type": "application/opds+json",
                    "templated": True,
                },
            ],
            "publications": result["results"],
            "facets": self._build_common_facets(
                url_fn,
                query,
                lang,
                copyrighted,
                audiobook,
                sort,
                sort_order,
                top_subjects,
            ),
        }
        self._append_pagination_links(feed["links"], build_url, result)
        return feed

    def _bookshelf_category(self, category: str):
        """List shelves in a category with sample groups. Navigation appears first, then groups."""
        found = next((cat for cat in CuratedBookshelves if cat.genre == category), None)
        if not found:
            raise cherrypy.HTTPError(404, "Category not found")

        shelves = [{"id": s[0], "name": s[1]} for s in found.shelves]
        groups = []
        book_counts = {}
        for s in shelves:
            try:
                q = self.fts.query(crosswalk=Crosswalk.OPDS)
                q.bookshelf_id(s["id"]).order_by(OrderBy.RANDOM)[1, SAMPLE_LIMIT]
                result = self.fts.execute(q)
                total = result.get("total", 0)
                book_counts[s["id"]] = total
                if result.get("results"):
                    groups.append(
                        {
                            "metadata": {"title": s["name"], "numberOfItems": total},
                            "links": [
                                {
                                    "href": f"/opds/bookshelves?id={s['id']}",
                                    "rel": "self",
                                    "type": "application/opds+json",
                                }
                            ],
                            "publications": result["results"],
                        }
                    )
            except Exception as e:
                cherrypy.log(
                    f"Error fetching bookshelf samples for shelf {s['id']}: {e}"
                )
                book_counts[s["id"]] = 0

        return {
            "metadata": {"title": category, "numberOfItems": len(shelves)},
            "links": [
                {
                    "rel": "self",
                    "href": f"/opds/bookshelves?category={quote(category)}",
                    "type": "application/opds+json",
                },
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {
                    "rel": "up",
                    "href": "/opds/bookshelves",
                    "type": "application/opds+json",
                },
            ],
            "navigation": [
                {
                    "href": f"/opds/bookshelves?id={s['id']}",
                    "title": f"{s['name']} ({book_counts.get(s['id'], 0)} books)",
                    "type": "application/opds+json",
                    "rel": "subsection",
                }
                for s in shelves
            ],
            "groups": groups,
        }

    # ========== LoCC ==========

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def loccs(
        self,
        parent: str = "",
        page: int = 1,
        limit: int = 28,
        query: str = "",
        lang: str = "",
        copyrighted: str = "",
        audiobook: str = "",
        sort: str = "",
        sort_order: str = "",
    ):
        """LoCC hierarchical navigation."""
        parent = (parent or "").strip().upper()
        page, limit = _parse_pagination(page, limit)

        try:
            children = self.fts.get_locc_children(parent)
        except Exception as e:
            cherrypy.log(f"LoCC children error: {e}")
            children = []

        # If children exist, return navigation
        if children:
            children.sort(key=lambda x: (len(x.get("code", "")), x.get("code", "")))

            # Get counts: subcategory counts for items with children, book counts for leaf nodes
            codes_with_children = [c["code"] for c in children if c.get("has_children")]
            codes_without_children = [
                c["code"] for c in children if not c.get("has_children")
            ]

            child_counts = (
                self._get_locc_child_counts(codes_with_children)
                if codes_with_children
                else {}
            )
            book_counts = (
                self._get_locc_book_counts(codes_without_children)
                if codes_without_children
                else {}
            )

            navigation = []
            for child in children:
                code = child["code"]
                raw_label = child.get("label", code)
                label = (
                    raw_label.split(":", 1)[1].strip()
                    if ":" in raw_label
                    else raw_label
                )
                has_children = child.get("has_children", False)

                if has_children:
                    count = child_counts.get(code, 0)
                    title = f"{label} ({count} subcategories)"
                else:
                    count = book_counts.get(code, 0)
                    title = f"{label} ({count} books)"

                navigation.append(
                    {
                        "href": f"/opds/loccs?parent={code}",
                        "title": title,
                        "type": "application/opds+json",
                        "rel": "subsection"
                        if has_children
                        else "subsection",
                    }
                )

            return {
                "metadata": {
                    "title": parent or "Subject Classification",
                    "numberOfItems": len(children),
                },
                "links": [
                    {
                        "rel": "self",
                        "href": f"/opds/loccs?parent={parent}"
                        if parent
                        else "/opds/loccs",
                        "type": "application/opds+json",
                    },
                    {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                    {
                        "rel": "up",
                        "href": "/opds/loccs" if parent else "/opds/",
                        "type": "application/opds+json",
                    },
                ],
                "navigation": navigation,
            }

        # No children -> leaf node: return books
        return self._locc_leaf(
            parent, page, limit, query, lang, copyrighted, audiobook, sort, sort_order
        )

    def _get_locc_child_counts(self, codes: list[str]) -> dict[str, int]:
        """Get subcategory counts for a list of LoCC codes."""
        if not codes:
            return {}
        return {code: len(self.fts.get_locc_children(code)) for code in codes}

    def _get_locc_book_counts(self, codes: list[str]) -> dict[str, int]:
        """Get book counts for a list of LoCC codes (leaf nodes)."""
        if not codes:
            return {}
        counts = {}
        for code in codes:
            q = self.fts.query().locc(code)
            counts[code] = self.fts.count(q)
        return counts

    def _locc_leaf(
        self,
        parent: str,
        page: int,
        limit: int,
        query: str,
        lang: str,
        copyrighted: str,
        audiobook: str,
        sort: str,
        sort_order: str,
    ):
        """Browse books in a LoCC leaf node."""
        try:
            q = self.fts.query(crosswalk=Crosswalk.OPDS)
            q.locc(parent)
            self._apply_filters(q, query, lang, copyrighted, audiobook)
            self._apply_sort(q, sort, sort_order, bool(query.strip()))
            q[page, limit]
            result = self.fts.execute(q)
        except Exception as e:
            cherrypy.log(f"LoCC browse error: {e}")
            raise cherrypy.HTTPError(500, "Browse failed")

        def build_url(p: int) -> str:
            return _url_with_params(
                "/opds/loccs",
                {
                    "parent": parent,
                    "query": query,
                    "page": p,
                    "limit": limit,
                    "lang": lang,
                    "copyrighted": copyrighted,
                    "audiobook": audiobook,
                    "sort": sort,
                    "sort_order": sort_order,
                },
            )

        def url_fn(q, lng, cr, ab, srt, srt_ord):
            return _url_with_params(
                "/opds/loccs",
                {
                    "parent": parent,
                    "query": q,
                    "page": 1,
                    "limit": limit,
                    "lang": lng,
                    "copyrighted": cr,
                    "audiobook": ab,
                    "sort": srt,
                    "sort_order": srt_ord,
                },
            )

        top_subjects = self._get_top_subjects(
            lambda: self.fts.query().locc(parent), query, lang, copyrighted, audiobook
        )

        feed = {
            "metadata": {
                "title": parent,
                "numberOfItems": result["total"],
                "itemsPerPage": result["page_size"],
                "currentPage": result["page"],
            },
            "links": [
                {
                    "rel": "self",
                    "href": build_url(result["page"]),
                    "type": "application/opds+json",
                },
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {"rel": "up", "href": "/opds/loccs", "type": "application/opds+json"},
                {
                    "rel": "search",
                    "href": f"/opds/loccs?parent={parent}{{&query}}",
                    "type": "application/opds+json",
                    "templated": True,
                },
            ],
            "publications": result["results"],
            "facets": self._build_common_facets(
                url_fn,
                query,
                lang,
                copyrighted,
                audiobook,
                sort,
                sort_order,
                top_subjects,
            ),
        }
        self._append_pagination_links(feed["links"], build_url, result)
        return feed

    # ========== Subjects ==========

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def subjects(
        self,
        id: int | None = None,
        page: int = 1,
        limit: int = 28,
        query: str = "",
        lang: str = "",
        copyrighted: str = "",
        audiobook: str = "",
        sort: str = "",
        sort_order: str = "",
    ):
        """Subject navigation and detail."""
        page, limit = _parse_pagination(page, limit)

        if id is not None:
            return self._subject_detail(
                int(id),
                page,
                limit,
                query,
                lang,
                copyrighted,
                audiobook,
                sort,
                sort_order,
            )

        # List top subjects
        subjects = self.fts.list_subjects()
        subjects.sort(key=lambda x: x["book_count"], reverse=True)
        return {
            "metadata": {"title": "Subjects", "numberOfItems": len(subjects)},
            "links": [
                {
                    "rel": "self",
                    "href": "/opds/subjects",
                    "type": "application/opds+json",
                },
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {"rel": "up", "href": "/opds/", "type": "application/opds+json"},
            ],
            "navigation": [
                {
                    "href": f"/opds/subjects?id={s['id']}",
                    "title": f"{s['name']} ({s['book_count']} books)",
                    "type": "application/opds+json",
                    "rel": "subsection",
                }
                for s in subjects[:100]
            ],
        }

    def _subject_detail(
        self,
        subject_id: int,
        page: int,
        limit: int,
        query: str,
        lang: str,
        copyrighted: str,
        audiobook: str,
        sort: str,
        sort_order: str,
    ):
        """Browse books for a specific subject."""
        subject_name = self.fts.get_subject_name(subject_id) or f"Subject {subject_id}"

        try:
            q = self.fts.query(crosswalk=Crosswalk.OPDS)
            q.subject_id(subject_id)
            self._apply_filters(q, query, lang, copyrighted, audiobook)
            self._apply_sort(q, sort, sort_order, bool(query.strip()))
            q[page, limit]
            result = self.fts.execute(q)
        except Exception as e:
            cherrypy.log(f"Subject browse error: {e}")
            raise cherrypy.HTTPError(500, "Browse failed")

        def build_url(p: int) -> str:
            return _url_with_params(
                "/opds/subjects",
                {
                    "id": subject_id,
                    "query": query,
                    "page": p,
                    "limit": limit,
                    "lang": lang,
                    "copyrighted": copyrighted,
                    "audiobook": audiobook,
                    "sort": sort,
                    "sort_order": sort_order,
                },
            )

        def url_fn(q, lng, cr, ab, srt, srt_ord):
            return _url_with_params(
                "/opds/subjects",
                {
                    "id": subject_id,
                    "query": q,
                    "page": 1,
                    "limit": limit,
                    "lang": lng,
                    "copyrighted": cr,
                    "audiobook": ab,
                    "sort": srt,
                    "sort_order": srt_ord,
                },
            )

        feed = {
            "metadata": {
                "title": subject_name,
                "numberOfItems": result["total"],
                "itemsPerPage": result["page_size"],
                "currentPage": result["page"],
            },
            "links": [
                {
                    "rel": "self",
                    "href": build_url(result["page"]),
                    "type": "application/opds+json",
                },
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {
                    "rel": "up",
                    "href": "/opds/subjects",
                    "type": "application/opds+json",
                },
                {
                    "rel": "search",
                    "href": f"/opds/subjects?id={subject_id}{{&query}}",
                    "type": "application/opds+json",
                    "templated": True,
                },
            ],
            "publications": result["results"],
            "facets": self._build_common_facets(
                url_fn, query, lang, copyrighted, audiobook, sort, sort_order
            ),
        }
        self._append_pagination_links(feed["links"], build_url, result)
        return feed

    # ========== Search ==========

    @cherrypy.expose
    @cherrypy.tools.json_out()
    def search(
        self,
        query: str = "",
        page: int = 1,
        limit: int = 28,
        field: str = "fuzzy",
        lang: str = "",
        copyrighted: str = "",
        audiobook: str = "",
        sort: str = "",
        sort_order: str = "",
        locc: str = "",
    ):
        """Full-text search with facets."""
        page, limit = _parse_pagination(page, limit)
        search_type = _parse_search_type(field)

        try:
            q = self.fts.query(crosswalk=Crosswalk.OPDS)
            if query.strip():
                q.search(query, search_type=search_type)

            self._apply_sort(q, sort, sort_order, bool(query.strip()))

            if lang:
                q.lang(lang)
            if copyrighted == "true":
                q.copyrighted()
            elif copyrighted == "false":
                q.public_domain()
            if audiobook == "true":
                q.audiobook()
            elif audiobook == "false":
                q.text_only()
            if locc:
                q.locc(locc)

            q[page, limit]
            result = self.fts.execute(q)

            top_subjects = None
            if query.strip() or locc or lang:
                top_subjects = self._get_top_subjects_for_search(
                    query, search_type, lang, copyrighted, audiobook, locc
                )
        except Exception as e:
            cherrypy.log(f"Search error: {e}")
            raise cherrypy.HTTPError(500, "Search failed")

        def url(p: int) -> str:
            return _url_with_params(
                "/opds/search",
                {
                    "query": query,
                    "page": p,
                    "limit": limit,
                    "field": field,
                    "lang": lang,
                    "copyrighted": copyrighted,
                    "audiobook": audiobook,
                    "sort": sort,
                    "sort_order": sort_order,
                    "locc": locc,
                },
            )

        feed = {
            "metadata": {
                "title": "Gutenberg Search Results",
                "numberOfItems": result["total"],
                "itemsPerPage": result["page_size"],
                "currentPage": result["page"],
            },
            "links": [
                {
                    "rel": "self",
                    "href": url(result["page"]),
                    "type": "application/opds+json",
                },
                {"rel": "start", "href": "/opds/", "type": "application/opds+json"},
                {"rel": "up", "href": "/opds/", "type": "application/opds+json"},
                {
                    "rel": "search",
                    "href": f"/opds/search?field={field}{{&query}}",
                    "type": "application/opds+json",
                    "templated": True,
                },
            ],
            "publications": result["results"],
            "facets": self._build_search_facets(
                query,
                limit,
                field,
                lang,
                copyrighted,
                audiobook,
                sort,
                sort_order,
                locc,
                top_subjects,
            ),
        }
        self._append_pagination_links(feed["links"], url, result)
        return feed

    def _get_top_subjects_for_search(
        self, query, search_type, lang, copyrighted, audiobook, locc
    ):
        """Get top subjects for search results."""
        try:
            q_sub = self.fts.query()
            if query.strip():
                q_sub.search(query, search_type=search_type)
            if lang:
                q_sub.lang(lang)
            if copyrighted == "true":
                q_sub.copyrighted()
            elif copyrighted == "false":
                q_sub.public_domain()
            if audiobook == "true":
                q_sub.audiobook()
            elif audiobook == "false":
                q_sub.text_only()
            if locc:
                q_sub.locc(locc)
            return self.fts.get_top_subjects_for_query(q_sub, limit=15, max_books=500)
        except Exception as e:
            cherrypy.log(f"Top subjects error: {e}")
            return None

    def _build_search_facets(
        self,
        query,
        limit,
        field,
        lang,
        copyrighted,
        audiobook,
        sort,
        sort_order,
        locc,
        top_subjects,
    ):
        """Build facets for search results including LoCC genre facet."""

        def url_fn(q, lng, cr, ab, srt, srt_ord):
            return _url_with_params(
                "/opds/search",
                {
                    "query": q,
                    "page": 1,
                    "limit": limit,
                    "field": field,
                    "lang": lng,
                    "copyrighted": cr,
                    "audiobook": ab,
                    "sort": srt,
                    "sort_order": srt_ord,
                    "locc": locc,
                },
            )

        facets = self._build_common_facets(
            url_fn, query, lang, copyrighted, audiobook, sort, sort_order, top_subjects
        )

        # Insert LoCC genre facet after sort
        locc_facet = {
            "metadata": {"title": "Broad Genre"},
            "links": [
                _facet_link(
                    _url_with_params(
                        "/opds/search",
                        {
                            "query": query,
                            "page": 1,
                            "limit": limit,
                            "field": field,
                            "lang": lang,
                            "copyrighted": copyrighted,
                            "audiobook": audiobook,
                            "sort": sort,
                            "sort_order": sort_order,
                            "locc": "",
                        },
                    ),
                    "Any",
                    not locc,
                )
            ]
            + [
                _facet_link(
                    _url_with_params(
                        "/opds/search",
                        {
                            "query": query,
                            "page": 1,
                            "limit": limit,
                            "field": field,
                            "lang": lang,
                            "copyrighted": copyrighted,
                            "audiobook": audiobook,
                            "sort": sort,
                            "sort_order": sort_order,
                            "locc": item.code,
                        },
                    ),
                    item.label,
                    locc == item.code,
                )
                for item in LoCCMainClass
            ],
        }
        # Insert after sort facet (index 1) or top subjects (index 2 if present)
        insert_pos = 2 if top_subjects else 1
        facets.insert(insert_pos, locc_facet)
        return facets


if __name__ == "__main__":
    cherrypy.config.update(
        {"server.socket_host": "0.0.0.0", "server.socket_port": 8080}
    )
    cherrypy.tree.mount(API(), "/opds", {"/": {}})
    try:
        cherrypy.engine.start()
        cherrypy.engine.block()
    except KeyboardInterrupt:
        cherrypy.engine.exit()
