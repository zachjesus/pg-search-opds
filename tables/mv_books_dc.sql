-- Aligned with libgutenberg DublinCoreMapping.py

BEGIN;

SET LOCAL work_mem = '256MB';
SET LOCAL maintenance_work_mem = '1GB';
SET LOCAL max_parallel_workers_per_gather = 4;

SET LOCAL client_min_messages = WARNING;

-- check if there is an equivalent already in PG we need to find and use it
CREATE OR REPLACE FUNCTION text_to_date_immutable(text) RETURNS date AS $$
    SELECT $1::date;
$$ LANGUAGE SQL IMMUTABLE STRICT;

-- check ifthere is an equivalent already in PG we need to find and use it
DO $$
BEGIN
    CREATE AGGREGATE tsvector_agg(tsvector) (
        SFUNC = tsvector_concat,
        STYPE = tsvector,
        INITCOND = ''
    );
EXCEPTION WHEN duplicate_function THEN
    NULL;
END $$;

DROP MATERIALIZED VIEW IF EXISTS mv_books_dc CASCADE;

CREATE MATERIALIZED VIEW mv_books_dc AS
SELECT
    b.pk AS book_id,
    b.title,
    b.tsvec,
    b.downloads,
    b.release_date,
    b.copyrighted,

    -- All authors sorted by heading then name (pipe-delimited for display)
    (
        SELECT STRING_AGG(au.author, ' | ' ORDER BY mba.heading, au.author)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        WHERE mba.fk_books = b.pk
    ) AS all_authors,

    -- All subjects sorted alphabetically (pipe-delimited for display)
    (
        SELECT STRING_AGG(s.subject, ' | ' ORDER BY s.subject)
        FROM mn_books_subjects mbs
        JOIN subjects s ON mbs.fk_subjects = s.pk
        WHERE mbs.fk_books = b.pk
    ) AS all_subjects,

    -- Combined searchable text: title + all authors + all subjects + all bookshelves
    -- (attributes excluded to reduce size for fuzzy search)
    -- LETS USE PROJECT GUTENBERG PREXISTING FOR THIS
    CONCAT_WS(' ',
        b.title,
        (SELECT STRING_AGG(au.author, ' ')
         FROM mn_books_authors mba
         JOIN authors au ON mba.fk_authors = au.pk
         WHERE mba.fk_books = b.pk),
        (SELECT STRING_AGG(s.subject, ' ')
         FROM mn_books_subjects mbs
         JOIN subjects s ON mbs.fk_subjects = s.pk
         WHERE mbs.fk_books = b.pk),
        (SELECT STRING_AGG(bs.bookshelf, ' ')
         FROM mn_books_bookshelves mbbs
         JOIN bookshelves bs ON mbbs.fk_bookshelves = bs.pk
         WHERE mbbs.fk_books = b.pk)
    ) AS book_text,

    -- All language codes as array for multi-language filtering
    COALESCE((
        SELECT ARRAY_AGG(DISTINCT l.pk::text)
        FROM mn_books_langs mbl
        JOIN langs l ON mbl.fk_langs = l.pk
        WHERE mbl.fk_books = b.pk
    ), ARRAY['en']::text[]) AS lang_codes,

    EXISTS (
        SELECT 1 FROM mn_books_categories mbc
        WHERE mbc.fk_books = b.pk AND mbc.fk_categories IN (1, 2)
    ) AS is_audio,

    (
        SELECT CASE
            WHEN a.text LIKE '%$b%' THEN
                TRIM(BOTH ' :;,.' FROM SPLIT_PART(SPLIT_PART(a.text, '$b', 2), '$', 1))
            ELSE NULL
        END
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.fk_attriblist = 245
        LIMIT 1
    ) AS subtitle,

    (
        SELECT MAX(au.born_floor)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        WHERE mba.fk_books = b.pk AND au.born_floor > 0
    ) AS max_author_birthyear,

    (
        SELECT MIN(au.born_floor)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        WHERE mba.fk_books = b.pk AND au.born_floor > 0
    ) AS min_author_birthyear,

    (
        SELECT MAX(au.died_floor)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        WHERE mba.fk_books = b.pk AND au.died_floor > 0
    ) AS max_author_deathyear,

    (
        SELECT MIN(au.died_floor)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        WHERE mba.fk_books = b.pk AND au.died_floor > 0
    ) AS min_author_deathyear,

    -- LoCC codes as array for fast filtering
    COALESCE((
        SELECT ARRAY_AGG(lc.pk)
        FROM mn_books_loccs mblc
        JOIN loccs lc ON mblc.fk_loccs = lc.pk
        WHERE mblc.fk_books = b.pk
    ), ARRAY[]::text[]) AS locc_codes,

    -- Reuse existing tsvec from authors table (already indexed there)
    COALESCE((
        SELECT tsvector_agg(au.tsvec)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        WHERE mba.fk_books = b.pk AND au.tsvec IS NOT NULL
    ), ''::tsvector) AS author_tsvec,

    COALESCE((
        SELECT tsvector_agg(s.tsvec)
        FROM mn_books_subjects mbs
        JOIN subjects s ON mbs.fk_subjects = s.pk
        WHERE mbs.fk_books = b.pk AND s.tsvec IS NOT NULL
    ), ''::tsvector) AS subject_tsvec,

    COALESCE((
        SELECT tsvector_agg(bs.tsvec)
        FROM mn_books_bookshelves mbbs
        JOIN bookshelves bs ON mbbs.fk_bookshelves = bs.pk
        WHERE mbbs.fk_books = b.pk AND bs.tsvec IS NOT NULL
    ), ''::tsvector) AS bookshelf_tsvec,

    COALESCE((
        SELECT tsvector_agg(a.tsvec)
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.tsvec IS NOT NULL
    ), ''::tsvector) AS attribute_tsvec,

    -- Bookshelf text for fuzzy/contains search
    (
        SELECT STRING_AGG(bs.bookshelf, ' ')
        FROM mn_books_bookshelves mbbs
        JOIN bookshelves bs ON mbbs.fk_bookshelves = bs.pk
        WHERE mbbs.fk_books = b.pk
    ) AS bookshelf_text,

    -- Attribute text for fuzzy/contains search (all MARC field text)
    -- Strip MARC subfield delimiters ($a, $b, $c, etc.)
    (
        SELECT STRING_AGG(
            REGEXP_REPLACE(a.text, '\$[a-z0-9]', ' ', 'gi'),
            ' '
        )
        FROM attributes a
        WHERE a.fk_books = b.pk
    ) AS attribute_text,

    -- Title tsvec (FTS on title alone)
    to_tsvector('english', COALESCE(b.title, '')) AS title_tsvec,

    -- Subtitle tsvec (FTS on subtitle alone)
    to_tsvector('english', COALESCE((
        SELECT CASE
            WHEN a.text LIKE '%$b%' THEN TRIM(SPLIT_PART(a.text, '$b', 2))
            ELSE NULL
        END
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.fk_attriblist = 245
        LIMIT 1
    ), '')) AS subtitle_tsvec,

    -- Creators (ordered for stable output)
    (
        SELECT ARRAY_AGG(au.pk ORDER BY mba.heading, r.role, au.author)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        JOIN roles r ON mba.fk_roles = r.pk
        WHERE mba.fk_books = b.pk
    ) AS creator_ids,
    (
        SELECT ARRAY_AGG(au.author ORDER BY mba.heading, r.role, au.author)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        JOIN roles r ON mba.fk_roles = r.pk
        WHERE mba.fk_books = b.pk
    ) AS creator_names,
    (
        SELECT ARRAY_AGG(r.role ORDER BY mba.heading, r.role, au.author)
        FROM mn_books_authors mba
        JOIN authors au ON mba.fk_authors = au.pk
        JOIN roles r ON mba.fk_roles = r.pk
        WHERE mba.fk_books = b.pk
    ) AS creator_roles,

    -- Subjects (ordered alphabetically)
    (
        SELECT ARRAY_AGG(s.pk ORDER BY s.subject)
        FROM mn_books_subjects mbs
        JOIN subjects s ON mbs.fk_subjects = s.pk
        WHERE mbs.fk_books = b.pk
    ) AS subject_ids,
    (
        SELECT ARRAY_AGG(s.subject ORDER BY s.subject)
        FROM mn_books_subjects mbs
        JOIN subjects s ON mbs.fk_subjects = s.pk
        WHERE mbs.fk_books = b.pk
    ) AS subject_names,

    -- Bookshelves (ordered alphabetically)
    (
        SELECT ARRAY_AGG(bs.pk ORDER BY bs.bookshelf)
        FROM mn_books_bookshelves mbbs
        JOIN bookshelves bs ON mbbs.fk_bookshelves = bs.pk
        WHERE mbbs.fk_books = b.pk
    ) AS bookshelf_ids,
    (
        SELECT ARRAY_AGG(bs.bookshelf ORDER BY bs.bookshelf)
        FROM mn_books_bookshelves mbbs
        JOIN bookshelves bs ON mbbs.fk_bookshelves = bs.pk
        WHERE mbbs.fk_books = b.pk
    ) AS bookshelf_names,

    -- DCMI types (default to Text)
    COALESCE((
        SELECT ARRAY_AGG(d.dcmitype ORDER BY d.dcmitype)
        FROM mn_books_categories mbc
        JOIN dcmitypes d ON mbc.fk_categories = d.pk
        WHERE mbc.fk_books = b.pk
    ), ARRAY['Text']::text[]) AS dcmitypes,

    -- Publisher (raw)
    (
        SELECT a.text
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.fk_attriblist IN (260, 264)
        ORDER BY a.fk_attriblist
        LIMIT 1
    ) AS publisher,

    -- Summary (MARC 520)
    (
        SELECT ARRAY_AGG(a.text ORDER BY a.pk)
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.fk_attriblist = 520
    ) AS summary,

    -- Credits (MARC 508) with "Updated:" stripped
    (
        SELECT ARRAY_AGG(
            TRIM(
                CASE
                    WHEN a.text ~* '\s*updated?:\s*'
                    THEN (regexp_split_to_array(a.text, '\s*[Uu][Pp][Dd][Aa][Tt][Ee][Dd]?:\s*'))[1]
                    ELSE a.text
                END
            )
            ORDER BY a.pk
        )
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.fk_attriblist = 508
    ) AS credits,

    -- Reading level (MARC 908)
    (
        SELECT a.text
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.fk_attriblist = 908
        ORDER BY a.pk
        LIMIT 1
    ) AS reading_level,

    -- Cover pages (MARC 901)
    (
        SELECT ARRAY_AGG(a.text ORDER BY a.pk)
        FROM attributes a
        WHERE a.fk_books = b.pk AND a.fk_attriblist = 901
    ) AS coverpage,

    -- Formats (files, ordered by filetype sort order)
    (
        SELECT ARRAY_AGG(f.filename ORDER BY ft.sortorder, f.fk_filetypes)
        FROM files f
        LEFT JOIN filetypes ft ON f.fk_filetypes = ft.pk
        WHERE f.fk_books = b.pk
          AND f.obsoleted = 0
          AND f.diskstatus = 0
    ) AS format_filenames,
    (
        SELECT ARRAY_AGG(f.fk_filetypes ORDER BY ft.sortorder, f.fk_filetypes)
        FROM files f
        LEFT JOIN filetypes ft ON f.fk_filetypes = ft.pk
        WHERE f.fk_books = b.pk
          AND f.obsoleted = 0
          AND f.diskstatus = 0
    ) AS format_filetypes,
    (
        SELECT ARRAY_AGG(ft.filetype ORDER BY ft.sortorder, f.fk_filetypes)
        FROM files f
        LEFT JOIN filetypes ft ON f.fk_filetypes = ft.pk
        WHERE f.fk_books = b.pk
          AND f.obsoleted = 0
          AND f.diskstatus = 0
    ) AS format_hr_filetypes,
    (
        SELECT ARRAY_AGG(ft.mediatype ORDER BY ft.sortorder, f.fk_filetypes)
        FROM files f
        LEFT JOIN filetypes ft ON f.fk_filetypes = ft.pk
        WHERE f.fk_books = b.pk
          AND f.obsoleted = 0
          AND f.diskstatus = 0
    ) AS format_mediatypes,
    (
        SELECT ARRAY_AGG(f.filesize ORDER BY ft.sortorder, f.fk_filetypes)
        FROM files f
        LEFT JOIN filetypes ft ON f.fk_filetypes = ft.pk
        WHERE f.fk_books = b.pk
          AND f.obsoleted = 0
          AND f.diskstatus = 0
    ) AS format_extents
FROM books b;

-- ============================================================================
-- INDEXES
-- ============================================================================

-- Primary key
CREATE UNIQUE INDEX idx_mv_pk ON mv_books_dc (book_id);

-- ============================================================================
-- B-TREE: Filtering & Sorting
-- ============================================================================
CREATE INDEX idx_mv_btree_downloads ON mv_books_dc (downloads DESC);
CREATE INDEX idx_mv_btree_copyrighted ON mv_books_dc (copyrighted);
CREATE INDEX idx_mv_gin_lang ON mv_books_dc USING GIN (lang_codes);
CREATE INDEX idx_mv_btree_is_audio ON mv_books_dc (is_audio) WHERE is_audio = true;
CREATE INDEX idx_mv_btree_birthyear_max ON mv_books_dc (max_author_birthyear) WHERE max_author_birthyear IS NOT NULL;
CREATE INDEX idx_mv_btree_birthyear_min ON mv_books_dc (min_author_birthyear) WHERE min_author_birthyear IS NOT NULL;
CREATE INDEX idx_mv_btree_deathyear_max ON mv_books_dc (max_author_deathyear) WHERE max_author_deathyear IS NOT NULL;
CREATE INDEX idx_mv_btree_deathyear_min ON mv_books_dc (min_author_deathyear) WHERE min_author_deathyear IS NOT NULL;
CREATE INDEX idx_mv_btree_release_date ON mv_books_dc (release_date DESC NULLS LAST);

-- ============================================================================
-- GIN: Array containment (locc_codes)
-- ============================================================================
CREATE INDEX idx_mv_gin_locc ON mv_books_dc USING GIN (locc_codes);

-- ============================================================================
-- GIN: Full-text search (tsvector)
-- ============================================================================
CREATE INDEX idx_mv_fts_book ON mv_books_dc USING GIN (tsvec);
CREATE INDEX idx_mv_fts_title ON mv_books_dc USING GIN (title_tsvec);
CREATE INDEX idx_mv_fts_subtitle ON mv_books_dc USING GIN (subtitle_tsvec); 
CREATE INDEX idx_mv_fts_author ON mv_books_dc USING GIN (author_tsvec);
CREATE INDEX idx_mv_fts_subject ON mv_books_dc USING GIN (subject_tsvec);
CREATE INDEX idx_mv_fts_bookshelf ON mv_books_dc USING GIN (bookshelf_tsvec);
CREATE INDEX idx_mv_fts_attribute ON mv_books_dc USING GIN (attribute_tsvec);

-- ============================================================================
-- GIN: Trigram ILIKE '%text%' (substring/contains search)
-- ============================================================================
CREATE INDEX idx_mv_contains_title ON mv_books_dc USING GIN (title gin_trgm_ops);
CREATE INDEX idx_mv_contains_subtitle ON mv_books_dc USING GIN (subtitle gin_trgm_ops);
CREATE INDEX idx_mv_contains_author ON mv_books_dc USING GIN (all_authors gin_trgm_ops);
CREATE INDEX idx_mv_contains_subject ON mv_books_dc USING GIN (all_subjects gin_trgm_ops);
CREATE INDEX idx_mv_contains_book ON mv_books_dc USING GIN (book_text gin_trgm_ops);
CREATE INDEX idx_mv_contains_bookshelf ON mv_books_dc USING GIN (bookshelf_text gin_trgm_ops);

-- ============================================================================
-- GiST: Trigram <% (fuzzy/typo-tolerant word similarity)
-- ============================================================================
CREATE INDEX idx_mv_fuzzy_title ON mv_books_dc USING GIST (title gist_trgm_ops);
CREATE INDEX idx_mv_fuzzy_subtitle ON mv_books_dc USING GIST (subtitle gist_trgm_ops);
CREATE INDEX idx_mv_fuzzy_author ON mv_books_dc USING GIST (all_authors gist_trgm_ops);
CREATE INDEX idx_mv_fuzzy_subject ON mv_books_dc USING GIST (all_subjects gist_trgm_ops);
CREATE INDEX idx_mv_fuzzy_book ON mv_books_dc USING GIST (book_text gist_trgm_ops);
CREATE INDEX idx_mv_fuzzy_bookshelf ON mv_books_dc USING GIST (bookshelf_text gist_trgm_ops);

ANALYZE mv_books_dc;

-- ============================================================================
-- Refresh Function (for use with systemd timer or cron)
-- ============================================================================

CREATE OR REPLACE FUNCTION refresh_mv_books_dc()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    -- Set memory for this session's refresh operation
    SET LOCAL work_mem = '256MB';
    SET LOCAL maintenance_work_mem = '1GB';

    REFRESH MATERIALIZED VIEW CONCURRENTLY mv_books_dc;
    ANALYZE mv_books_dc;
END;
$$;

-- To manually refresh: SELECT refresh_mv_books_dc();
-- For systemd timer, use: psql -U postgres -d your_database -c "SELECT refresh_mv_books_dc();"

COMMIT;
