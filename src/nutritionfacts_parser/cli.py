import os
import json
import click
import logging
from tqdm import tqdm
from urllib.parse import urlparse
import concurrent.futures

from .scraper import NutritionFactsScraper
from .parser import parse_video_page

def setup_logging(verbose: bool, log_dir: str = None):
    """Configures the logging format and level, writing to both console (colored) and a rotating file (plain)."""
    level = logging.DEBUG if verbose else logging.INFO
    
    console_handler = logging.StreamHandler()
    handlers = [console_handler]
    
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        log_filepath = os.path.join(log_dir, "parser.log")
        from logging.handlers import RotatingFileHandler
        # 5MB per log file, keep up to 3 backup files
        file_handler = RotatingFileHandler(log_filepath, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        handlers.append(file_handler)
        
    root_logger = logging.getLogger()
    for h in root_logger.handlers[:]:
        root_logger.removeHandler(h)
        
    logging.basicConfig(
        level=level,
        handlers=handlers
    )

def get_slug(url: str) -> str:
    """Extracts a filesystem-friendly slug from the video URL."""
    path = urlparse(url).path.strip("/")
    # e.g., "video/why-might-vegetarians-develop-less-depression"
    # we want "why-might-vegetarians-develop-less-depression"
    parts = path.split("/")
    if len(parts) >= 2 and parts[0] == "video":
        return parts[1]
    return parts[-1] if parts else "video"

def process_video(url: str, scraper: NutritionFactsScraper, metadata_dir: str, transcript_dir: str, resume: bool, verbose: bool) -> str:
    """Fetch and parse a video page.

    Returns:
        "scraped" if successful,
        "skipped" if file existed and resume is True,
        "error" on failure.
    """
    slug = get_slug(url)
    meta_filepath = os.path.join(metadata_dir, f"{slug}.json")
    transcript_filepath = os.path.join(transcript_dir, f"{slug}.txt")

    # Skip if resume is enabled and metadata file exists
    if resume and os.path.exists(meta_filepath):
        return "skipped"

    html = scraper.fetch_video_page_html(url)
    if not html:
        logging.getLogger("nutritionfacts_parser").error(f"Failed to fetch HTML for: {url}")
        return "error"

    try:
        parsed_data = parse_video_page(html, url)
        with open(meta_filepath, "w", encoding="utf-8") as f:
            json.dump(parsed_data, f, indent=2, ensure_ascii=False)
        with open(transcript_filepath, "w", encoding="utf-8") as f:
            f.write(parsed_data.get("transcript_clean", ""))
        return "scraped"
    except Exception as e:
        logging.getLogger("nutritionfacts_parser").error(
            f"Failed to parse page {url}: {e}", exc_info=verbose
        )
        return "error"

@click.command()
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, dir_okay=True, writable=True),
    default="data",
    help="Directory to save scraped metadata and transcripts (defaults to './data')."
)
@click.option(
    "--delay",
    "-d",
    type=float,
    default=1.0,
    help="Delay in seconds between HTTP requests to be polite."
)
@click.option(
    "--limit",
    "-l",
    type=int,
    default=None,
    help="Limit the number of videos to parse (useful for testing)."
)
@click.option(
    "--sitemap-url",
    default="https://nutritionfacts.org/sitemap.xml",
    help="Main sitemap XML index URL."
)
@click.option(
    "--resume/--no-resume",
    default=True,
    help="Resume scraping by skipping already downloaded metadata files."
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose debug logs."
)
def main(output_dir: str, delay: float, limit: int, sitemap_url: str, resume: bool, verbose: bool):
    """
    Scaffolds and runs the parser/scraper for NutritionFacts.org videos.
    """
    # Establish subdirectories
    metadata_dir = os.path.join(output_dir, "metadata")
    transcript_dir = os.path.join(output_dir, "transcripts")
    log_dir = os.path.join(output_dir, "logs")
    
    setup_logging(verbose, log_dir)
    logger = logging.getLogger("nutritionfacts_parser")
    
    os.makedirs(metadata_dir, exist_ok=True)
    os.makedirs(transcript_dir, exist_ok=True)
    
    logger.info(f"Output directories: {metadata_dir} and {transcript_dir}")
    
    scraper = NutritionFactsScraper(delay_seconds=delay)
    
    logger.info("Discovering video sitemaps and metadata...")
    full_metadata = scraper.get_all_video_page_metadata(index_url=sitemap_url)
    # Derive URLs from the metadata entries, ignoring unwanted patterns
    video_urls = [entry["url"] for entry in full_metadata if "url" in entry]
    # Save full metadata (lastmod, changefreq, priority) for each URL discovered
    metadata_path = os.path.join(output_dir, "urls_metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(full_metadata, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved full URL metadata to {metadata_path}")
    
    if not video_urls:
        logger.warning("No video URLs discovered. Exiting.")
        return
        
    logger.info(f"Discovered {len(video_urls)} video pages in total.")
    
    # Apply limit if specified
    if limit is not None:
        video_urls = video_urls[:limit]
        logger.info(f"Limiting crawl to first {limit} video pages.")

    scraped_count = 0
    skipped_count = 0
    error_count = 0
    
    # Crawl videos with parallel processing and a proper in‑place tqdm progress bar
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        # Submit all video processing tasks
        futures = {executor.submit(process_video, url, scraper, metadata_dir, transcript_dir, resume, verbose): url for url in video_urls}
        # Use tqdm context manager for a single in‑place progress bar
        with tqdm(total=len(video_urls), desc="Processing videos", unit="video", leave=False) as pbar:
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result == "scraped":
                    scraped_count += 1
                elif result == "skipped":
                    skipped_count += 1
                else:
                    error_count += 1
                pbar.update(1)

    logger.info("--- Crawl Summary ---")
    logger.info(f"Total urls processed: {len(video_urls)}")
    logger.info(f"Successfully scraped: {scraped_count}")
    logger.info(f"Skipped (already crawled): {skipped_count}")
    logger.info(f"Failed/Errors: {error_count}")
    logger.info("---------------------")

if __name__ == "__main__":
    main()
