"""CLI interface for amz-scout."""

import logging
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from amz_scout.browser import BrowserError, BrowserSession, check_browser_use_installed
from amz_scout.config import (
    MarketplaceConfig,
    ProjectConfig,
    load_marketplace_config,
    load_project_config,
    validate_config,
)
from amz_scout.csv_io import (
    merge_competitive,
    merge_price_history,
    read_competitive_data,
    read_price_history,
    write_competitive_data,
    write_price_history,
)
from amz_scout.marketplace import setup_marketplace
from amz_scout.models import CompetitiveData, PriceHistory, Product
from amz_scout.scraper.amazon import scrape_product_page
from amz_scout.scraper.keepa import KeepaClient
from amz_scout.scraper.search import resolve_asin_via_search
from amz_scout.utils import today_iso

app = typer.Typer(name="amz-scout", help="Amazon competitive data scraping automation tool")
console = Console()

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def _resolve_config_paths(project_config: str) -> tuple[Path, Path]:
    """Resolve project and marketplace config file paths."""
    project_path = Path(project_config)
    if not project_path.exists():
        project_path = CONFIG_DIR / project_config
    marketplace_path = project_path.parent / "marketplaces.yaml"
    return project_path, marketplace_path


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


@app.command()
def scrape(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    marketplace: str | None = typer.Option(None, "-m", "--marketplace", help="Single marketplace"),
    product: str | None = typer.Option(None, "-p", "--product", help="Single product model"),
    exclude: str | None = typer.Option(None, "-x", "--exclude", help="Exclude product (substring match)"),
    data_only: bool = typer.Option(False, help="Skip Keepa price history"),
    history_only: bool = typer.Option(False, help="Only fetch Keepa price history"),
    detailed: bool = typer.Option(False, help="Keepa detailed mode: include seller data (~5 tokens/product vs 1)"),
    headed: bool = typer.Option(False, help="Show browser window"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Scrape Amazon competitive data + Keepa price history."""
    _setup_logging(verbose)
    project_path, mp_path = _resolve_config_paths(project_config)

    proj = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)

    errors = validate_config(proj, marketplaces)
    if errors:
        for e in errors:
            console.print(f"[red]Config error:[/] {e}")
        raise typer.Exit(1)

    # Filter targets
    target_sites = [marketplace] if marketplace else proj.target_marketplaces
    products = [p.to_product() for p in proj.products]
    if product:
        products = [p for p in products if product.lower() in p.model.lower()]
        if not products:
            console.print(f"[red]No product matching '{product}'[/]")
            raise typer.Exit(1)
    if exclude:
        products = [p for p in products if exclude.lower() not in p.model.lower()]
        if not products:
            console.print(f"[red]All products excluded by '{exclude}'[/]")
            raise typer.Exit(1)

    headed_mode = headed or proj.settings.headed_mode
    output_base = Path(proj.project.output_dir)
    output_base.mkdir(parents=True, exist_ok=True)

    # ── Price history (Keepa) ──
    if not data_only:
        _scrape_price_history(
            proj, products, target_sites, marketplaces, output_base, headed_mode,
            detailed=detailed,
        )

    # ── Amazon product pages (browser needed) ──
    if not history_only:
        if not check_browser_use_installed():
            console.print("[red]browser-use CLI not found.[/] Install: uv tool install browser-use")
            raise typer.Exit(1)

        _scrape_amazon(
            proj, products, target_sites, marketplaces,
            output_base, project_path, headed_mode,
        )

    console.print("\n[green bold]Done![/]")


def _scrape_price_history(
    proj: ProjectConfig,
    products: list[Product],
    target_sites: list[str],
    marketplaces: dict[str, MarketplaceConfig],
    output_base: Path,
    headed: bool = False,
    detailed: bool = False,
) -> None:
    """Fetch Keepa price history."""
    mode_label = "detailed ~5 tok/product" if detailed else "basic 1 tok/product"
    console.print(f"\n[bold]── Price History ({mode_label}) ──[/]")

    # Try Keepa first
    keepa: KeepaClient | None = None
    try:
        keepa = KeepaClient()
        console.print(f"  Keepa API: [green]available[/] ({keepa.tokens_left} tokens)")
    except ValueError:
        console.print("  Keepa API: [yellow]unavailable[/] (no API key)")

    for site in target_sites:
        mp_config = marketplaces.get(site)
        if not mp_config:
            continue

        if not (keepa and mp_config.keepa_domain_code):
            console.print(f"  [cyan]{site}[/]: [red]no Keepa domain code configured[/]")
            continue

        if keepa.tokens_left < len(products):
            console.print(
                f"  [cyan]{site}[/]: [yellow]waiting for tokens "
                f"({keepa.tokens_left} < {len(products)})[/]"
            )

        raw_dir = output_base / "data" / mp_config.region / "raw"
        histories = keepa.fetch_price_history(
            products, site, mp_config.keepa_domain,
            keepa_domain_code=mp_config.keepa_domain_code,
            detailed=detailed,
            raw_dir=raw_dir,
        )

        if histories:
            has_data = sum(1 for h in histories if h.buybox_current is not None)
            console.print(f"  [cyan]{site}[/]: {has_data}/{len(histories)} with price")
            data_dir = output_base / "data" / mp_config.region
            csv_path = data_dir / f"{site.lower()}_price_history.csv"
            merged = merge_price_history(read_price_history(csv_path), histories)
            write_price_history(merged, csv_path)

    if keepa:
        remaining = keepa.tokens_left
        total_products = len(products) * len(target_sites)
        tok_per = 5 if detailed else 1
        tokens_used = total_products * tok_per
        console.print(f"  Keepa: ~{tokens_used} tokens used, {remaining} remaining")
        if remaining < total_products * tok_per:
            refill_mins = (total_products * tok_per - remaining)
            console.print(f"  [dim]Next full run needs ~{refill_mins} min refill[/]")


def _scrape_amazon(
    proj: ProjectConfig,
    products: list[Product],
    target_sites: list[str],
    marketplaces: dict[str, MarketplaceConfig],
    output_base: Path,
    project_path: Path,
    headed: bool,
) -> None:
    """Scrape Amazon product pages across target marketplaces."""
    console.print("\n[bold]── Amazon Product Pages ──[/]")

    for site in target_sites:
        mp_config = marketplaces.get(site)
        if not mp_config:
            continue

        console.print(f"\n[bold cyan]  {site} ({mp_config.amazon_domain})[/]")
        browser = BrowserSession(headed=headed, session=f"amz-scout-{site.lower()}")

        try:
            setup_marketplace(browser, site, mp_config)
            results: list[CompetitiveData] = []

            for i, prod in enumerate(products, 1):
                # Check for warning notes
                note = prod.note_for(site)
                if note and "not listed" in note.lower():
                    console.print(f"  [{i}/{len(products)}] {prod.brand} {prod.model}: [dim]skipped ({note})[/]")
                    results.append(CompetitiveData(
                        date=today_iso(), site=site, category=prod.category,
                        brand=prod.brand, model=prod.model, asin=prod.asin_for(site),
                        title="", price="N/A", rating="N/A", review_count="N/A",
                        bought_past_month="N/A", bsr="N/A", available="Not listed",
                        url="",
                    ))
                    continue

                # Scrape with retry
                data = None
                for attempt in range(proj.settings.retry_count):
                    data = scrape_product_page(
                        browser, prod, site, mp_config,
                        page_load_wait=proj.settings.page_load_wait,
                    )
                    if data is not None:
                        break
                    if attempt < proj.settings.retry_count - 1:
                        time.sleep(2)

                if data is None:
                    # Try search fallback
                    console.print(f"  [{i}/{len(products)}] {prod.brand} {prod.model}: [yellow]ASIN not found, searching...[/]")
                    found_asin = resolve_asin_via_search(
                        browser, prod, site, mp_config, config_path=project_path,
                    )
                    if found_asin:
                        # Retry with found ASIN
                        from dataclasses import replace
                        updated = Product(
                            category=prod.category, brand=prod.brand, model=prod.model,
                            default_asin=found_asin, search_keywords=prod.search_keywords,
                            marketplace_overrides=prod.marketplace_overrides,
                        )
                        data = scrape_product_page(
                            browser, updated, site, mp_config,
                            page_load_wait=proj.settings.page_load_wait,
                        )

                if data is None:
                    console.print(f"  [{i}/{len(products)}] {prod.brand} {prod.model}: [red]Not found[/]")
                    results.append(CompetitiveData(
                        date=today_iso(), site=site, category=prod.category,
                        brand=prod.brand, model=prod.model, asin=prod.asin_for(site),
                        title="", price="N/A", rating="N/A", review_count="N/A",
                        bought_past_month="N/A", bsr="N/A", available="Not listed",
                        url="",
                    ))
                else:
                    console.print(
                        f"  [{i}/{len(products)}] {data.brand} {data.model}: "
                        f"{data.price} | {data.rating} | {data.review_count}"
                    )
                    results.append(data)

                if i < len(products):
                    time.sleep(proj.settings.inter_product_delay)

            # Final write
            _save_competitive(results, output_base, mp_config, site)
            has_price = sum(1 for r in results if r.price not in ("N/A", "", "Currently unavailable"))
            console.print(f"  [green]{site}: {has_price}/{len(results)} with price[/]")

            # Data validation
            _validate_results(results, site)

        except Exception as e:
            # Save whatever we have so far on unexpected crash
            if results:
                _save_competitive(results, output_base, mp_config, site)
                console.print(f"  [yellow]{site}: saved {len(results)} partial results before error[/]")
            console.print(f"  [red]{site}: {e}[/]")

        finally:
            browser.close()


def _save_competitive(
    results: list[CompetitiveData],
    output_base: Path,
    mp_config: MarketplaceConfig,
    site: str,
) -> None:
    """Save competitive data CSV, merging with existing data if present."""
    data_dir = output_base / "data" / mp_config.region
    csv_path = data_dir / f"{site.lower()}_competitive_data.csv"
    merged = merge_competitive(read_competitive_data(csv_path), results)
    write_competitive_data(merged, csv_path)


@app.command()
def discover(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    marketplace: str | None = typer.Option(None, "-m", "--marketplace"),
    headed: bool = typer.Option(False),
    verbose: bool = typer.Option(False, "-v"),
) -> None:
    """Discover cross-marketplace ASINs and auto-update config."""
    _setup_logging(verbose)
    project_path, mp_path = _resolve_config_paths(project_config)

    proj = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)

    target_sites = [marketplace] if marketplace else proj.target_marketplaces
    products = [p.to_product() for p in proj.products]

    if not check_browser_use_installed():
        console.print("[red]browser-use CLI not found.[/]")
        raise typer.Exit(1)

    console.print("[bold]ASIN Discovery Scan[/]")

    for site in target_sites:
        mp_config = marketplaces.get(site)
        if not mp_config:
            continue

        console.print(f"\n[bold cyan]{site}[/]")
        browser = BrowserSession(headed=headed, session=f"amz-scout-discover-{site.lower()}")

        try:
            setup_marketplace(browser, site, mp_config)
            found_count = 0

            for prod in products:
                try:
                    asin = prod.asin_for(site)
                    url = f"https://www.{mp_config.amazon_domain}/dp/{asin}"
                    browser.open(url)
                    time.sleep(2)

                    # Check if product truly exists with an active offer
                    result = browser.evaluate("""(function() {
                        var title = document.getElementById('productTitle')?.innerText?.trim();
                        var price = document.querySelector('.a-price .a-offscreen')?.innerText?.trim();
                        var bodyText = document.body.innerText || '';

                        // Page doesn't exist at all
                        if (!title && !price) return JSON.stringify({exists: false, reason: 'no_page'});

                        // Page exists but has no active offer (title present, no price)
                        var noOffer = bodyText.includes('No featured offers')
                            || bodyText.includes('Currently unavailable')
                            || bodyText.includes('Derzeit nicht verfügbar')
                            || bodyText.includes('Momentan nicht verfügbar')
                            || bodyText.includes('cannot be dispatched');
                        if (title && !price && noOffer) {
                            return JSON.stringify({exists: false, reason: 'no_offer', title: title.substring(0, 60)});
                        }

                        // Has title + price = valid product
                        if (title && price) return JSON.stringify({exists: true, title: title.substring(0, 60)});

                        // Has title but no price and no known error — treat as suspicious
                        if (title && !price) return JSON.stringify({exists: false, reason: 'no_price'});

                        return JSON.stringify({exists: false, reason: 'unknown'});
                    })()""")

                    if result.get("exists"):
                        console.print(f"  {prod.model}: [green]OK[/] ({asin})")
                    else:
                        reason = result.get("reason", "unknown")
                        console.print(
                            f"  {prod.model}: [yellow]{reason}[/] — searching..."
                        )
                        found = resolve_asin_via_search(
                            browser, prod, site, mp_config, config_path=project_path,
                        )
                        if found:
                            console.print(f"  {prod.model}: [green]Found[/] {found} (saved)")
                            found_count += 1
                        else:
                            console.print(f"  {prod.model}: [red]Not found[/]")

                except BrowserError as e:
                    console.print(f"  {prod.model}: [red]Error: {e}[/]")

                time.sleep(1)

            console.print(f"  New ASINs discovered: {found_count}")

        finally:
            browser.close()


@app.command()
def validate(
    project_config: str = typer.Argument(help="Path to project YAML config"),
) -> None:
    """Validate configuration files."""
    project_path, mp_path = _resolve_config_paths(project_config)

    try:
        proj = load_project_config(project_path)
        marketplaces = load_marketplace_config(mp_path)
    except Exception as e:
        console.print(f"[red]Config load error:[/] {e}")
        raise typer.Exit(1)

    errors = validate_config(proj, marketplaces)
    if errors:
        for e in errors:
            console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(1)

    console.print(f"[green]Config valid![/]")
    console.print(f"  Project: {proj.project.name}")
    console.print(f"  Markets: {', '.join(proj.target_marketplaces)}")
    console.print(f"  Products: {len(proj.products)}")


@app.command()
def status(
    project_config: str = typer.Argument(help="Path to project YAML config"),
) -> None:
    """Check data completeness for a project."""
    project_path, mp_path = _resolve_config_paths(project_config)
    proj = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)
    output_base = Path(proj.project.output_dir)

    table = Table(title="Data Status")
    table.add_column("Site")
    table.add_column("Competitive Data")
    table.add_column("Price History")

    for site in proj.target_marketplaces:
        mp = marketplaces.get(site)
        if not mp:
            continue
        data_dir = output_base / "data" / mp.region
        site_lower = site.lower()
        comp_file = data_dir / f"{site_lower}_competitive_data.csv"
        hist_file = data_dir / f"{site_lower}_price_history.csv"

        comp_status = f"[green]{_count_lines(comp_file)} rows[/]" if comp_file.exists() else "[red]missing[/]"
        hist_status = f"[green]{_count_lines(hist_file)} rows[/]" if hist_file.exists() else "[red]missing[/]"
        table.add_row(site, comp_status, hist_status)

    console.print(table)


def _validate_results(results: list[CompetitiveData], site: str) -> None:
    """Validate scraped data and warn about anomalies."""
    from amz_scout.utils import parse_price, parse_rating

    warnings = []
    for r in results:
        if r.price in ("N/A", "", "Currently unavailable"):
            continue
        price = parse_price(r.price)
        if price is not None and (price < 1 or price > 5000):
            warnings.append(f"{r.brand} {r.model}: suspicious price {r.price}")
        rating = parse_rating(r.rating)
        if rating is not None and (rating < 1 or rating > 5):
            warnings.append(f"{r.brand} {r.model}: invalid rating {r.rating}")
        if r.title and len(r.title) < 10:
            warnings.append(f"{r.brand} {r.model}: suspiciously short title")

    if warnings:
        console.print(f"  [yellow]Warnings ({site}):[/]")
        for w in warnings:
            console.print(f"    [yellow]! {w}[/]")


@app.command()
def reparse(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    marketplace: str | None = typer.Option(None, "-m", "--marketplace"),
) -> None:
    """Regenerate price history CSVs from saved raw JSON (zero token cost)."""
    _setup_logging()
    project_path, mp_path = _resolve_config_paths(project_config)
    proj = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)
    products = [p.to_product() for p in proj.products]
    target_sites = [marketplace] if marketplace else proj.target_marketplaces
    output_base = Path(proj.project.output_dir)

    from amz_scout.scraper.keepa import _parse_product, _empty_history
    import json as json_mod

    console.print("[bold]── Reparse from raw JSON ──[/]")

    for site in target_sites:
        mp_config = marketplaces.get(site)
        if not mp_config:
            continue

        raw_dir = output_base / "data" / mp_config.region / "raw"
        if not raw_dir.exists():
            console.print(f"  [cyan]{site}[/]: [yellow]no raw/ directory[/]")
            continue

        histories = []
        for prod in products:
            asin = prod.asin_for(site)
            json_path = raw_dir / f"{site.lower()}_{asin}.json"
            if json_path.exists():
                with open(json_path) as f:
                    raw = json_mod.load(f)
                histories.append(_parse_product(prod, site, raw, detailed=False))
            else:
                histories.append(_empty_history(prod, site))

        has_data = sum(1 for h in histories if h.buybox_current is not None)
        console.print(f"  [cyan]{site}[/]: {has_data}/{len(histories)} reparsed from raw JSON")

        data_dir = output_base / "data" / mp_config.region
        csv_path = data_dir / f"{site.lower()}_price_history.csv"
        write_price_history(histories, csv_path)

    console.print("[green bold]Done![/]")


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path) as f:
        return sum(1 for _ in f) - 1  # Subtract header


if __name__ == "__main__":
    app()
