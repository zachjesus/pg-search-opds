import html
from typing import Any
from itertools import zip_longest

from .constants import Crosswalk, Language
from .helpers import format_dict_result

LANGUAGE_LABELS = {lang.code: lang.label for lang in Language}

def _rights_text(copyrighted: int | None) -> str:
    return (
        "Copyrighted. Read the copyright notice inside this book for details."
        if copyrighted
        else "Public domain in the USA."
    )


def _build_creators(row) -> list[dict[str, Any]]:
    names = list(row.creator_names) if row.creator_names else []
    roles = list(row.creator_roles) if row.creator_roles else []
    ids = list(row.creator_ids) if row.creator_ids else []
    creators = []
    for name, role, cid in zip_longest(names, roles, ids, fillvalue=None):
        if not name:
            continue
        creators.append({"id": cid, "name": name, "role": role or "Author"})
    return creators


def _build_subjects(row) -> list[dict[str, Any]]:
    names = list(row.subject_names) if row.subject_names else []
    ids = list(row.subject_ids) if row.subject_ids else []
    return [
        {"id": sid, "subject": name}
        for name, sid in zip_longest(names, ids, fillvalue=None)
        if name
    ]


def _build_bookshelves(row) -> list[dict[str, Any]]:
    names = list(row.bookshelf_names) if row.bookshelf_names else []
    ids = list(row.bookshelf_ids) if row.bookshelf_ids else []
    return [
        {"id": bid, "bookshelf": name}
        for name, bid in zip_longest(names, ids, fillvalue=None)
        if name
    ]


def _build_formats(row) -> list[dict[str, Any]]:
    filenames = list(row.format_filenames) if row.format_filenames else []
    filetypes = list(row.format_filetypes) if row.format_filetypes else []
    hr_filetypes = list(row.format_hr_filetypes) if row.format_hr_filetypes else []
    mediatypes = list(row.format_mediatypes) if row.format_mediatypes else []
    extents = list(row.format_extents) if row.format_extents else []
    results = []
    for fn, ftype, hr, med, extent in zip_longest(
        filenames, filetypes, hr_filetypes, mediatypes, extents, fillvalue=None
    ):
        if not fn:
            continue
        results.append(
            {
                "filename": fn,
                "filetype": ftype,
                "hr_filetype": hr,
                "mediatype": med,
                "extent": extent,
            }
        )
    return results


@format_dict_result
def crosswalk_full(row) -> dict[str, Any]:
    creators = _build_creators(row)
    subjects = _build_subjects(row)
    bookshelves = _build_bookshelves(row)
    formats = _build_formats(row)
    language = [
        {"code": code, "name": LANGUAGE_LABELS.get(code, code)}
        for code in (list(row.lang_codes) if row.lang_codes else [])
        if code
    ]
    coverage = [
        {"id": code, "locc": code}
        for code in (list(row.locc_codes) if row.locc_codes else [])
        if code
    ]
    dcmitypes = [{"dcmitype": t} for t in (list(row.dcmitypes) if row.dcmitypes else []) if t]
    summary = list(row.summary) if row.summary else []
    credits = list(row.credits) if row.credits else []
    coverpage = list(row.coverpage) if row.coverpage else []
    return {
        "book_id": row.book_id,
        "title": row.title,
        "author": row.all_authors,
        "downloads": row.downloads,
        "creators": creators,
        "language": language,
        "subjects": subjects,
        "bookshelves": bookshelves,
        "date": row.release_date,
        "format": formats,
        "coverpage": coverpage,
        "summary": summary,
        "credits": credits,
        "type": dcmitypes,
        "rights": _rights_text(row.copyrighted),
        "coverage": coverage,
        "publisher": {"raw": row.publisher} if row.publisher else None,
    }


@format_dict_result
def crosswalk_mini(row) -> dict[str, Any]:
    return {
        "id": row.book_id,
        "title": row.title,
        "author": row.all_authors,
        "downloads": row.downloads,
    }


@format_dict_result
def crosswalk_pg(row) -> dict[str, Any]:
    creators = _build_creators(row)
    subjects = [s["subject"] for s in _build_subjects(row) if s.get("subject")]
    bookshelves = [
        b["bookshelf"] for b in _build_bookshelves(row) if b.get("bookshelf")
    ]
    language = [
        {"code": code, "name": LANGUAGE_LABELS.get(code, code)}
        for code in (list(row.lang_codes) if row.lang_codes else [])
        if code
    ]
    formats = _build_formats(row)
    return {
        "ebook_no": row.book_id,
        "title": row.title,
        "contributors": [
            {"name": c.get("name"), "role": c.get("role", "Author")}
            for c in creators
        ],
        "language": language,
        "subjects": subjects,
        "bookshelves": bookshelves,
        "release_date": row.release_date,
        "downloads_last_30_days": row.downloads,
        "files": [
            {
                "filename": f.get("filename"),
                "type": f.get("mediatype"),
                "size": f.get("extent"),
            }
            for f in formats
            if f.get("filename")
        ],
        "cover_url": (list(row.coverpage) if row.coverpage else [None])[0],
    }


@format_dict_result
def crosswalk_opds(row) -> dict[str, Any]:
    """Transform row to OPDS 2.0 publication format per spec."""
    creators = _build_creators(row)
    subjects = [s["subject"] for s in _build_subjects(row) if s.get("subject")]
    bookshelves = _build_bookshelves(row)
    formats = _build_formats(row)
    locc_codes = [c for c in (list(row.locc_codes) if row.locc_codes else []) if c]

    metadata = {
        "@type": "http://schema.org/Book",
        "identifier": f"urn:gutenberg:{row.book_id}",
        "title": row.title,
        "language": (list(row.lang_codes) if row.lang_codes else ["en"])[0] or "en",
    }

    if creators and creators[0].get("name"):
        p = creators[0]
        author = {"name": p["name"], "sortAs": p["name"]}
        if p.get("id"):
            author["identifier"] = f"https://www.gutenberg.org/ebooks/author/{p['id']}"
        metadata["author"] = author

    if row.release_date:
        metadata["published"] = row.release_date

    desc_parts = []
    if summary := (list(row.summary) if row.summary else [None])[0]:
        desc_parts.append(summary)
    if credits := (list(row.credits) if row.credits else [None])[0]:
        desc_parts.append(f"Credits: {credits}")
    if row.reading_level:
        desc_parts.append(f"Reading Level: {row.reading_level}")
    if dcmitype := [t for t in (list(row.dcmitypes) if row.dcmitypes else []) if t]:
        desc_parts.append(f"Category: {', '.join(dcmitype)}")
    desc_parts.append(f"Rights: {_rights_text(row.copyrighted)}")
    desc_parts.append(f"Downloads: {row.downloads}")

    if desc_parts:
        metadata["description"] = (
            "<p>" + "</p><p>".join(html.escape(p) for p in desc_parts) + "</p>"
        )

    subjects += locc_codes
    if subjects:
        metadata["subject"] = subjects

    if row.publisher:
        metadata["publisher"] = row.publisher

    collections = []
    for b in bookshelves:
        if b.get("bookshelf"):
            collections.append(
                {
                    "name": b["bookshelf"],
                    "identifier": f"https://www.gutenberg.org/ebooks/bookshelf/{b.get('id', '')}",
                }
            )
    for code in locc_codes:
        collections.append(
            {
                "name": code,
                "identifier": f"https://www.gutenberg.org/ebooks/locc/{code}",
            }
        )
    if collections:
        metadata["belongsTo"] = {"collection": collections}

    links = []

    # Audiobooks: use HTML index | Text books: prefer EPUB3 with images
    target_format = "index" if row.is_audio else "epub3.images"
    fallback_formats = ["epub.images", "epub.noimages", "kindle.images", "pdf.images", "pdf.noimages", "html"] if not row.is_audio else ["html"]

    # Try target format first, then fallbacks
    for try_format in [target_format] + fallback_formats:
        for f in formats:
            fn = f.get("filename")
            if not fn:
                continue
            ftype = (f.get("filetype") or "").strip().lower()
            if ftype != try_format:
                continue

            href = (
                fn
                if fn.startswith(("http://", "https://"))
                else f"https://www.gutenberg.org/{fn.lstrip('/')}"
            )
            mtype = (f.get("mediatype") or "").strip()

            link = {
                "rel": "http://opds-spec.org/acquisition/open-access",
                "href": href,
                "type": mtype or "application/epub+zip",
            }
            if f.get("extent") is not None and f["extent"] > 0:
                link["length"] = f["extent"]
            if f.get("hr_filetype"):
                link["title"] = f["hr_filetype"]
            links.append(link)
            break
        if links:
            break

    # OPDS 2.0 requires at least one acquisition link - fallback to readable HTML page
    if not links:
        links.append({
            "rel": "http://opds-spec.org/acquisition/open-access",
            "href": f"https://www.gutenberg.org/ebooks/{row.book_id}",
            "type": "text/html",
        })

    result = {"metadata": metadata, "links": links}

    images = []
    for f in formats:
        ft = f.get("filetype") or ""
        fn = f.get("filename")
        if fn and ("cover.medium" in ft or ("cover" in ft and not images)):
            href = (
                fn
                if fn.startswith(("http://", "https://"))
                else f"https://www.gutenberg.org/{fn.lstrip('/')}"
            )
            img = {"href": href, "type": "image/jpeg"}
            images.append(img)
            if "cover.medium" in ft:
                break
    if images:
        result["images"] = images

    return result


CROSSWALK_MAP = {
    Crosswalk.FULL: crosswalk_full,
    Crosswalk.MINI: crosswalk_mini,
    Crosswalk.PG: crosswalk_pg,
    Crosswalk.OPDS: crosswalk_opds,
}
