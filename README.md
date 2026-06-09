# NutritionFacts.org Sitemap & Transcript Parser

A CLI-based Python tool to parse sitemaps from [NutritionFacts.org](https://nutritionfacts.org), discover all video pages, extract video metadata (from JSON-LD structure), clean and save transcripts, and manage local storage.

## Features

- **Automated Sitemap Parsing**: Reads the main sitemap index `sitemap.xml`, extracts all `video-sitemap*.xml` sub-sitemaps, and discovers video page links automatically.
- **Robust Metadata Extraction**: Parses structured JSON-LD (`VideoObject`) to extract properties such as title, description, duration, upload date, thumbnail URL, and YouTube video/embed link.
- **Transcript Extraction**: Targets the transcript section (in both desktop tab panels and mobile accordion elements) to extract paragraphs and clean them by removing website boilerplate (e.g., volunteers requests, approximate audio notes).
- **Polite Crawling**: Automatically adds configurable rate-limiting delays between requests and implements custom user-agent headers.
- **Scrape Resuming**: Skips previously downloaded files to resume work without starting over.
- **Export Formats**: Saves video details as standard structured JSON files under `metadata/` and transcripts as plain text files under `transcripts/`.

## Project Structure

```
nutritionfactsorg_llmwiki/
├── pyproject.toml              # Build settings and python packages metadata
├── requirements.txt            # Dependency list
├── README.md                   # Documentation
└── src/
    └── nutritionfacts_parser/
        ├── __init__.py         # Package init
        ├── __main__.py         # Entry point for python -m execution
        ├── cli.py              # CLI handler (click)
        ├── parser.py           # HTML and JSON-LD parsing logic
        └── scraper.py          # Network requests, session management & sitemaps
```

## Installation

### Prerequisites
- Python >= 3.8

### 1. Set Up Virtual Environment with uv (Recommended)
```bash
uv venv
. .venv/bin/activate
```

### 2. Install Dependencies using uv
```bash
uv pip install -e .
```
Or directly from `requirements.txt`:
```bash
pip install -r requirements.txt
```

## Usage

You can run the script using the command-line interface:

```bash
# Run package as a module
python3 -m nutritionfacts_parser --help
```

### Examples

#### Basic Run (processes all video sitemaps with default settings)
```bash
python3 -m nutritionfacts_parser --output-dir ./crawled_data
```

#### Test run (limit to first 5 videos to check output)
```bash
python3 -m nutritionfacts_parser --output-dir ./test_data --limit 5 --verbose
```

#### Custom Delay (polite scraper with 2.5-second request interval)
```bash
python3 -m nutritionfacts_parser --output-dir ./crawled_data --delay 2.5
```

## Output Structure

The scraper creates the specified `--output-dir` (default: `./data/`) containing two sub-folders:

### 1. `metadata/`
Contains JSON files representing each video, named after the video page slug:
```json
{
  "url": "https://nutritionfacts.org/video/blocking-the-cancer-metastasis-enzyme-mmp-9-with-beans-and-chickpeas/",
  "title": "Blocking the Cancer Metastasis Enzyme MMP-9 with Beans",
  "description": "Which legumes are best at inhibiting the matrix metalloproteinase enzymes that allow cancer to become invasive?",
  "upload_date": "2022-04-13T07:50:55+00:00",
  "duration": "PT6M12S",
  "thumbnail_url": "http://i.ytimg.com/vi/XJsame1wVc0/maxresdefault.jpg",
  "video_url": "https://youtu.be/XJsame1wVc0",
  "embed_url": "https://youtube.com/embed/XJsame1wVc0",
  "transcript_raw": [
    "Below is an approximation of this video’s audio content...",
    "Although we’re spending billions on fancy new types...",
    "..."
  ],
  "transcript_clean": "Although we’re spending billions on fancy new types...\n\nIn a previous video, I talked about..."
}
```

### 2. `transcripts/`
Contains plain text files containing the cleaned transcript corresponding to the video slug:
```
Although we’re spending billions on fancy new types of chemotherapy, the overflowing sink that is cancer treatment is expected to rise by about 70 percent over the next two decades...

In a previous video, I talked about the impact of diet and nutrition on the ten hallmarks of cancer...
```
