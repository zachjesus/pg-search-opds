# Gutenberg OPDS 2.0 and Full-Text Search

A PostgreSQL-backed full-text search engine for Project Gutenberg and the new OPDS 2.0 catalog.

<img width="1700" height="414" alt="image" src="https://github.com/user-attachments/assets/0d90690f-cb01-42c3-be9d-800f01857922" />

<img width="1659" height="608" alt="image" src="https://github.com/user-attachments/assets/e701c1d6-9263-484b-bc07-6bedbc3dc399" />

<img width="1648" height="964" alt="image" src="https://github.com/user-attachments/assets/3a0eee9e-e6c1-4854-aabf-2b0598576864" />

<img width="1682" height="616" alt="image" src="https://github.com/user-attachments/assets/226f9562-679f-430e-9a48-cec4ff7d075b" />

<img width="1409" height="904" alt="image" src="https://github.com/user-attachments/assets/e714f27d-9032-4dbe-8d07-b4b008bbd528" />

https://github.com/user-attachments/assets/4e75d60b-e6ca-4ba6-a72d-c112ecfef28f

Video of DB and Search Engine Interface running on a server with 1GB ram and 1 core CPU. Search returns results with FULL metadata in around 20-30ms in most cases even on minimal hardware.

## Requirements

### Python Dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

### PostgreSQL Extensions

The materialized view requires these extensions:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;    -- Fuzzy/trigram search
CREATE EXTENSION IF NOT EXISTS pg_prewarm; -- Cache warming
```

### Database Setup

1. Import the Project Gutenberg database
2. Build the materialized view:
   ```bash
   psql -U postgres -d gutendb -f mv_books_dc.sql
   ```
3. Prewarm indexes into memory:
   ```bash
   psql -U postgres -d gutendb -f prewarm_mv_books_dc.sql
   ```

## Materialized View: mv_books_dc

Denormalized view containing all book metadata for fast searching.

### Columns

| Column | Type | Description |
|--------|------|-------------|
| book_id | int | Primary key |
| title | text | Book title |
| all_authors | text | Pipe-delimited author names |
| downloads | int | Download count (last 30 days) |
| release_date | date | Publication date (denormalized for fast sorting) |
| lang_codes | text[] | Array of language codes |
| copyrighted | int | 0 = public domain, 1 = copyrighted |
| is_audio | bool | True if audiobook |
| locc_codes | text[] | Library of Congress classification codes |
| creator_ids | int[] | Creator IDs aligned to creator_names/roles |
| creator_names | text[] | Creator names aligned to creator_ids/roles |
| creator_roles | text[] | Creator roles aligned to creator_ids/names |
| subject_ids | int[] | Subject IDs aligned to subject_names |
| subject_names | text[] | Subject names aligned to subject_ids |
| bookshelf_ids | int[] | Bookshelf IDs aligned to bookshelf_names |
| bookshelf_names | text[] | Bookshelf names aligned to bookshelf_ids |
| dcmitypes | text[] | DCMI type labels (defaults to Text) |
| publisher | text | Publisher raw string |
| summary | text[] | MARC 520 summary entries |
| credits | text[] | MARC 508 credits entries |
| reading_level | text | MARC 908 reading level |
| coverpage | text[] | MARC 901 cover URLs |
| format_filenames | text[] | File URLs aligned to format_* arrays |
| format_filetypes | text[] | Filetype codes aligned to format_* arrays |
| format_hr_filetypes | text[] | Human-readable filetype labels |
| format_mediatypes | text[] | Media types aligned to format_* arrays |
| format_extents | bigint[] | File sizes aligned to format_* arrays |

### Indexes

| Type | Columns | Use Case |
|------|---------|----------|
| B-tree | downloads, release_date, copyrighted, is_audio, author birth/death years | Sorting, equality filters |
| GIN tsvector | tsvec | Full-text search |
| GiST trigram | book_text | Fuzzy/typo-tolerant search |
| GIN array | lang_codes | Array containment |

### Refresh

The MV must be refreshed periodically to reflect database changes:

```sql
SELECT refresh_mv_books_dc();
```

Or via cron/systemd timer:

```bash
psql -U postgres -d gutendb -c "SELECT refresh_mv_books_dc();"
```

## FullTextSearch API

### Basic Usage

```python
from FullTextSearch import FullTextSearch, SearchType, OrderBy, Crosswalk

fts = FullTextSearch()

# Simple search
result = fts.execute(fts.query().search("Shakespeare")[1, 28])
print(result["total"], result["results"])

# Get single book
book = fts.get(1342)

# Get multiple books
books = fts.get_many([1342, 84, 11])

# Count only
count = fts.count(fts.query().search("Shakespeare"))
```

### Search Types

| Type | Operator | Index | Speed | Use Case |
|------|----------|-------|-------|----------|
| FTS | `@@` websearch_to_tsquery | GIN tsvector | Fast | Stemmed word matching, supports operators |
| FUZZY | `<%` word_similarity | GiST trigram | Slower | Typo tolerance ("Shakspeare" matches "Shakespeare") |

**FTS (Full-Text Search)** - Default, fastest option:
- Uses PostgreSQL `websearch_to_tsquery` 
- Supports boolean operators (see below)
- Default sort: **Relevance** 
- Best for: Precise searches, exact word matching, speed

**Fuzzy Search** - Typo-tolerant (slower but still fast):
- Uses trigram similarity for typo tolerance
- Default sort: **Relevance** 
- Best for: User input with potential misspellings

```python
from FullTextSearch import SearchType

# FTS (default) - fast, supports operators
q.search("Shakespeare")

# Fuzzy - typo-tolerant, slower
q.search("Shakspeare", SearchType.FUZZY)

```

### FTS Search Operators

FTS mode supports PostgreSQL `websearch_to_tsquery` syntax:

| Operator | Example | Meaning |
|----------|---------|---------|
| `"phrase"` | `"to be or not to be"` | Exact phrase match |
| `word1 word2` | `shakespeare hamlet` | AND (both words required) |
| `word1 or word2` | `romeo or juliet` | OR (either word) |
| `-word` | `shakespeare -hamlet` | NOT (exclude word) |

**Examples:**
- `"exact phrase"` - Finds exact phrase
- `adventure novel` - Books with both "adventure" AND "novel"
- `twain or clemens` - Books with "twain" OR "clemens"
- `science -fiction` - Books with "science" but NOT "fiction"

### Search Fields

Search currently targets book text only (title/authors/subjects/bookshelves combined).

### Filter Methods

All filters use optimized MN table joins or indexed columns for fast performance.

```python
from FullTextSearch import LanguageCode, LoccClass, FileType, Encoding

# By ID
q.etext(1342)                    # Single book
q.etexts([1342, 84, 11])         # Multiple books

# By downloads
q.downloads_gte(1000)            # >= 1000 downloads
q.downloads_lte(100)             # <= 100 downloads

# By copyright
q.public_domain()                # copyrighted = 0
q.copyrighted()                  # copyrighted = 1

# By language 
q.lang(LanguageCode.EN)          # English
q.lang(LanguageCode.DE)          # German

# By format
q.text_only()                    # Exclude audiobooks
q.audiobook()                    # Only audiobooks

# By author dates
q.author_born_after(1900)
q.author_born_before(1700)
q.author_died_after(1950)
q.author_died_before(1800)

# By release date (
q.released_after("2020-01-01")
q.released_before("2000-01-01")

# By classification 
q.locc(LoccClass.P)              # LoCC top-level class (prefix match)
q.locc("PS")                     # Optional: narrower LoCC prefix match

# By ID 
q.author_id(53)                  # Mark Twain
q.subject_id(1)                  # Uses mn_books_subjects
q.bookshelf_id(68)               # Uses mn_books_bookshelves
q.contributor_role("Illustrator")

# Custom SQL
q.where("COALESCE(array_length(creator_ids, 1), 0) > :n", n=2)
```

### Chaining

All methods return `self` for chaining:

```python
from FullTextSearch import OrderBy, LanguageCode

result = fts.execute(
    fts.query()
    .search("Adventure")
    .lang(LanguageCode.EN)
    .public_domain()
    .downloads_gte(100)
    .order_by(OrderBy.DOWNLOADS)
    [1, 28]
)
```

### Ordering

```python
from FullTextSearch import OrderBy, SortDirection

q.order_by(OrderBy.RELEVANCE)                    # ts_rank_cd (FTS) or word_similarity (Fuzzy)
q.order_by(OrderBy.DOWNLOADS)                   
q.order_by(OrderBy.TITLE)
q.order_by(OrderBy.AUTHOR)
q.order_by(OrderBy.RELEASE_DATE)                
q.order_by(OrderBy.RANDOM)
q.order_by(OrderBy.DOWNLOADS, SortDirection.ASC) # Explicit direction
```

### Pagination

```python
q[1, 28]   # Page 1, 28 results per page (default)
q[3, 50]   # Page 3, 50 results per page
q[2]       # Page 2, default 28 per page
```

### Output Formats (Crosswalks)

```python
from FullTextSearch import Crosswalk

fts.query(Crosswalk.FULL)   # Full: book_id, title, author, downloads, dc (computed)
fts.query(Crosswalk.MINI)   # Minimal: id, title, author, downloads
fts.query(Crosswalk.PG)     # Project Gutenberg API format
fts.query(Crosswalk.OPDS)   # OPDS 2.0 publication format
fts.query(Crosswalk.CUSTOM) # Custom transformer (see below)
```

### Custom Crosswalks

```python
def my_format(row):
    return {
        "id": row.book_id,
        "name": row.title,
        "author": row.all_authors,
    }

fts.set_custom_transformer(my_format)
result = fts.execute(fts.query(Crosswalk.CUSTOM).search("Shakespeare"))
```

### Result Format

```python
{
    "results": [...],      # List of books in crosswalk format
    "page": 1,             # Current page
    "page_size": 28,       # Results per page
    "total": 1234,         # Total matching books
    "total_pages": 45      # Total pages
}
```

## OPDS 2.0 Server

### What is OPDS?

OPDS (Open Publication Distribution System) is a standard for distributing digital publications. OPDS 2.0 uses JSON feeds that e-reader apps (like Thorium, FBReader, etc.) can browse to discover and download books.

### Running the Server

```bash
python OPDS.py
```

Server runs at `http://127.0.0.1:8080/opds/`

### OPDS Catalog Structure

### Endpoints

#### Root Catalog
```
GET /opds/
```
Returns homepage with:
- Navigation links (Search Fuzzy, Search FTS, Browse by LoCC, Bookshelf, Subjects Most Popular, Recently Added, Random)
- Groups: All curated bookshelves with 20 sample publications each

#### Search
```
GET /opds/search
GET /opds/search?query=shakespeare
GET /opds/search?query=twain&field=fts_keyword&sort=relevance
```

**Search Types:**
- **Fuzzy** (`field=fuzzy_keyword`): Typo-tolerant, slower, sorts by downloads by default
- **FTS** (`field=fts_keyword`): Fast, exact matching, supports operators, sorts by relevance by default

**Query Parameters:**
| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| query | Search text | (empty) | Search query (supports operators in FTS mode) |
| field | `keyword`, `fts_keyword`, `fuzzy_keyword`, `title`, `author`, etc. | `keyword` (fuzzy) | Search field and type |
| lang | Language code (en, de, fr, etc.) | (any) | Filter by language |
| copyrighted | `true`, `false` | (any) | Filter by copyright status |
| audiobook | `true`, `false` | (any) | Filter by format |
| locc | LoCC code (A, B, PS, etc.) | (any) | Filter by Library of Congress classification |
| sort | `downloads`, `relevance`, `title`, `author`, `release_date`, `random` | `relevance` (FTS) or `downloads` (Fuzzy) | Sort order |
| sort_order | `asc`, `desc` | (default per field) | Sort direction |
| page | Page number | 1 | Pagination |
| limit | 1-100 | 28 | Results per page |

**FTS Operators** (when using `field=fts_keyword`):
- `"exact phrase"` - Phrase matching
- `word1 word2` - AND (both required)
- `word1 or word2` - OR (either word)
- `-word` - NOT (exclude)

**Example searches:**
- `/opds/search?query=shakespeare` - Fuzzy search (typo-tolerant)
- `/opds/search?query=shakespeare&field=fts_keyword` - FTS search (fast, exact)
- `/opds/search?query="romeo and juliet"` - Exact phrase (FTS)
- `/opds/search?query=twain or clemens` - OR operator (FTS)
- `/opds/search?query=science -fiction` - Exclude word (FTS)

#### Browse by LoCC (Subject Classification)
```
GET /opds/loccs
GET /opds/loccs?parent=P
GET /opds/loccs?parent=PS&query=novel
```

Hierarchical navigation through Library of Congress Classification:
- Without `parent`: Shows top-level classes (A, B, C, D, E, F, G, H, J, K, L, M, N, P, Q, R, S, T, U, V, Z)
- With `parent`: Shows subclasses or books (if leaf node)
- Supports search and filtering within any LoCC class
- Shows **Top Subjects** facet when viewing books

#### Bookshelves
```
GET /opds/bookshelves
GET /opds/bookshelves?id=644
GET /opds/bookshelves?id=644&query=adventure
```

Curated bookshelves organized by category.

#### Subjects
```
GET /opds/subjects
GET /opds/subjects?id=1
GET /opds/subjects?id=1&query=novel
```

Lists top 50 or so subjects.

#### LOCC
```
GET /opds/genres
GET /opds/genres?code=P
GET /opds/genres?code=P&query=poetry
```

Allows users to explore the LOCC Heirarchy.  

### Facets (Dynamic Filters)

Facets appear on publication pages (search results, browse results) and provide dynamic filtering options:

**Sort By:**
- Most Popular (downloads)
- Relevance
- Title (A-Z)
- Author (A-Z)
- Random

**Top Subjects in Results:**
- Appears when query or filters are active
- Shows up to 15 most common subjects in current results
- Clicking a subject navigates to that subject page (facet disappears)

**Broad Genre:**
- Library of Congress top-level classes
- Only shown in main search (not in LoCC browse)

**Copyright Status:**
- Any / Public Domain / Copyrighted

**Format:**
- Any / Text / Audiobook

**Language:**
- Any / English / German / French / etc.

### Navigation Behavior

- **Search**: Preserves search type (Fuzzy/FTS) and context (bookshelf, subject, LoCC)
- **Back navigation**: All pages include `rel="start"` link to homepage
- **Up navigation**: Parent pages include `rel="up"` link
- **Search template**: Uses `{?query}` syntax for OPDS-compliant search URIs

## Testing

```bash
python test.py
```

Runs all search types, filters, and crosswalks with timing output.

## Architecture

```mermaid
flowchart TB
    subgraph Server["CherryPy OPDS Server"]
        TP[Thread Pool]
    end

    subgraph Stateless["FullTextSearch (stateless)"]
        FTS[FullTextSearch]
        ENGINE[SQLAlchemy Engine]
        FACTORY[Session Factory]
    end

    subgraph Stateful["Per-Request Objects"]
        SQ[SearchQuery]
        SESS[Session]
    end

    subgraph Pool["SQLAlchemy Pool"]
        C1[Connection 1]
        C2[Connection 2]
        CN[Connection N]
    end

    subgraph DB["PostgreSQL"]
        MV[Materialized View]
        SB[Shared Buffers]
    end

    TP --> FTS
    FTS -->|"create_engine()"| ENGINE
    ENGINE -->|"bind"| FACTORY
    FTS -->|".query()"| SQ
    SQ -->|".search().lang()[1,28]"| SQ
    FTS -->|".execute(q)"| FACTORY
    FACTORY -->|"with Session()"| SESS
    SESS -->|"checkout"| Pool
    Pool --> DB 
```

**Typical usage:**
```python
fts = FullTextSearch()                          # Creates engine + session factory
q = fts.query().search("Shakespeare")[1, 28]    # Builds SearchQuery (no DB hit)
result = fts.execute(q)                         # Session checks out connection, runs query
```
