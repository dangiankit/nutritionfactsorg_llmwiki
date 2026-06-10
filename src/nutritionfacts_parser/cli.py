import os
import json
import click
import logging
import shutil
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
    """Extract a filesystem‑friendly slug from a content URL.
    Example paths: "video/why-might-vegetarians-develop-less-depression" or "audio/xyz".
    Returns the identifier after the first path segment.
    """
    path = urlparse(url).path.strip('/')
    parts = path.split('/')
    if len(parts) >= 2:
        return parts[1]
    return parts[-1] if parts else "content"

def process_video(url: str, scraper: NutritionFactsScraper, metadata_dir: str, transcript_dir: str, resume: bool, verbose: bool, content_type: str = "other") -> str:
    """
    Fetch and parse a page for the given content type, storing files under per‑type subfolders.

    Returns:
        "scraped" if successful,
        "skipped" if file existed and resume is True,
        "error" on failure.
    """
    slug = get_slug(url)
    # Ensure per‑type subdirectories exist
    meta_subdir = os.path.join(metadata_dir, content_type)
    transcript_subdir = os.path.join(transcript_dir, content_type)
    os.makedirs(meta_subdir, exist_ok=True)
    os.makedirs(transcript_subdir, exist_ok=True)
    meta_filepath = os.path.join(meta_subdir, f"{slug}.json")
    transcript_filepath = os.path.join(transcript_subdir, f"{slug}.txt")
    # Skip if resume is enabled and metadata file exists
    if resume and os.path.exists(meta_filepath):
        return "skipped"
    html = scraper.fetch_video_page_html(url)
    if not html:
        logging.getLogger("nutritionfacts_parser").error(f"Failed to fetch HTML for: {url}")
        return "error"
    try:
        parsed_data = parse_video_page(html, url, content_type)
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
    help="Limit the number of items to parse (useful for testing)."
)
@click.option(
    "--sitemap-url",
    default="https://nutritionfacts.org/sitemap.xml",
    help="Main sitemap XML index URL."
)
@click.option(
    "--list-sitemaps",
    is_flag=True,
    help="Print all discovered sitemap URLs and exit."
)
@click.option(
    "--content-type",
    "-c",
    type=str,
    multiple=True,
    help="Filter by content type (e.g., video, audio, hnta-video). If omitted, all types are included."
)
@click.option(
    "--clean/--no-clean",
    default=False,
    help="Delete the output directory before crawling."
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
def main(
    output_dir: str,
    delay: float,
    limit: int,
    sitemap_url: str,
    content_type: tuple = (),
    list_sitemaps: bool = False,
    clean: bool = False,
    resume: bool = True,
    verbose: bool = False,
):
    """
    Scaffolds and runs the parser/scraper for NutritionFacts.org content items.
    """
    # Clean output directory if requested
    if clean:
        if os.path.isdir(output_dir):
            try:
                shutil.rmtree(output_dir)
                logging.getLogger("nutritionfacts_parser").info(f"Removed existing output directory: {output_dir}")
            except Exception as e:
                logging.getLogger("nutritionfacts_parser").error(f"Failed to clean output directory {output_dir}: {e}")
        # Re‑create subdirectories after clean
    # Establish subdirectories
    metadata_dir = os.path.join(output_dir, "metadata")
    transcript_dir = os.path.join(output_dir, "transcripts")
    log_dir = os.path.join(output_dir, "logs")
    
    setup_logging(verbose, log_dir)
    logger = logging.getLogger("nutritionfacts_parser")
    # Normalize content_type arguments to lowercase and support comma‑separated values
    if content_type:
        flattened = []
        for val in content_type:
            for part in val.split(','):
                part = part.strip()
                if part:
                    flattened.append(part.lower())
        content_type = tuple(flattened)

    scraper = NutritionFactsScraper(delay_seconds=delay)

    logger.info("Discovering sitemaps and metadata...")
    full_metadata = scraper.get_all_video_page_metadata(index_url=sitemap_url)

    # Build a mapping from content type to its URLs
    type_to_urls: dict[str, list[str]] = {}
    for entry in full_metadata:
        ct = entry.get("content_type", "other")
        if content_type and ct not in content_type:
            continue
        url = entry.get("url")
        if not url:
            continue
        type_to_urls.setdefault(ct, []).append(url)

    # Apply global limit if specified (applies per type proportionally)
    if limit is not None:
        total = sum(len(urls) for urls in type_to_urls.values())
        if total > limit:
            scale = limit / total
            for ct in list(type_to_urls):
                new_len = max(1, int(len(type_to_urls[ct]) * scale))
                type_to_urls[ct] = type_to_urls[ct][:new_len]

    # Save full metadata for reference
    metadata_path = os.path.join(output_dir, "urls_metadata.json")
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(full_metadata, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved full URL metadata to {metadata_path}")

    if not type_to_urls:
        logger.warning("No URLs discovered for the specified content type(s). Exiting.")
        return

    def _process_batch(ct: str, urls: list[str]) -> tuple[int, int, int, list[str]]:
        """Process URLs for a given content type and return (scraped, skipped, error, error_messages)."""
        scraped = skipped = errors = 0
        error_messages = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    process_video,
                    url,
                    scraper,
                    metadata_dir,
                    transcript_dir,
                    resume,
                    verbose,
                    ct,
                ): url
                for url in urls
            }
            with tqdm(total=len(urls), desc=f"Processing {ct}", unit="item", leave=False) as pbar:
                for future in concurrent.futures.as_completed(futures):
                    result = future.result()
                    if result == "scraped":
                        scraped += 1
                    elif result == "skipped":
                        skipped += 1
                    else:
                        errors += 1
                        error_messages.append(f"Error processing {futures[future]}")
                    pbar.update(1)
        return scraped, skipped, errors, error_messages

    # Run each content‑type batch in parallel, each with its own progress bar
    overall_scraped = overall_skipped = overall_errors = 0
    all_error_messages = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(type_to_urls)) as type_executor:
        type_futures = {
            type_executor.submit(_process_batch, ct, urls): ct
            for ct, urls in type_to_urls.items()
        }
        for fut in concurrent.futures.as_completed(type_futures):
            ct = type_futures[fut]
            s, sk, e, batch_errors = fut.result()
            overall_scraped += s
            overall_skipped += sk
            overall_errors += e
            all_error_messages.extend(batch_errors)

    logger.info("--- Crawl Summary ---")
    logger.info(f"Total urls processed: {sum(len(urls) for urls in type_to_urls.values())}")
    logger.info(f"Successfully scraped: {overall_scraped}")
    logger.info(f"Skipped (already crawled): {overall_skipped}")
    logger.info(f"Failed/Errors: {overall_errors}")
    if all_error_messages:
        logger.info("--- Errors Encountered ---")
        for msg in all_error_messages:
            logger.error(msg)
if __name__ == "__main__":
    main()
