"""CLI interface for amz-scout."""

import logging
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from amz_scout.browser import BrowserSession, check_browser_use_installed
from amz_scout.config import (
    MarketplaceConfig,
    ProjectConfig,
    load_marketplace_config,
    load_project_config,
    validate_config,
)
from amz_scout.csv_io import write_competitive_data, write_price_history
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
    data_only: bool = typer.Option(False, help="Skip Keepa price history"),
    history_only: bool = typer.Option(False, help="Only fetch Keepa price history"),
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

    headed_mode = headed or proj.settings.headed_mode
    output_base = Path(proj.project.output_dir)
    output_base.mkdir(parents=True, exist_ok=True)

    # ── Price history (Keepa → CamelCamelCamel fallback) ──
    if not data_only:
        _scrape_price_history(
            proj, products, target_sites, marketplaces, output_base, headed_mode,
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
) -> None:
    """Fetch price history: Keepa first, CamelCamelCamel as fallback."""
    console.print("\n[bold]── Price History ──[/]")

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

        histories = keepa.fetch_price_history(
            products, site, mp_config.keepa_domain,
            keepa_domain_code=mp_config.keepa_domain_code,
        )

        if histories:
            has_data = sum(1 for h in histories if h.buybox_current is not None)
            console.print(f"  [cyan]{site}[/]: {has_data}/{len(histories)} with price")
            data_dir = output_base / "data" / mp_config.region
            write_price_history(histories, data_dir / f"{site.lower()}_price_history.csv")

    if keepa:
        console.print(f"  Keepa tokens remaining: {keepa.tokens_left}")


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

                data = scrape_product_page(browser, prod, site, mp_config)

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
                        data = scrape_product_page(browser, updated, site, mp_config)

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

            # Write CSV
            data_dir = output_base / "data" / mp_config.region
            write_competitive_data(results, data_dir / f"{site.lower()}_competitive_data.csv")
            has_price = sum(1 for r in results if r.price not in ("N/A", "", "Currently unavailable"))
            console.print(f"  [green]{site}: {has_price}/{len(results)} with price[/]")

        finally:
            browser.close()


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
                asin = prod.asin_for(site)
                url = f"https://www.{mp_config.amazon_domain}/dp/{asin}"
                browser.open(url)
                time.sleep(2)

                # Check if product exists (must have title OR price to be valid)
                result = browser.evaluate("""(function() {
                    var title = document.getElementById('productTitle')?.innerText?.trim();
                    var price = document.querySelector('.a-price .a-offscreen')?.innerText?.trim();
                    var bodyText = document.body.innerText || '';
                    var notFound = bodyText.includes('not a functioning page')
                        || bodyText.includes('nicht funktionierend')
                        || bodyText.includes('looking for')
                        || bodyText.length < 500;
                    if (!title && !price && notFound) {
                        return JSON.stringify({exists: false});
                    }
                    if (!title && !price) {
                        return JSON.stringify({exists: false});
                    }
                    return JSON.stringify({exists: true, title: (title || '').substring(0, 60)});
                })()""")

                if result.get("exists"):
                    console.print(f"  {prod.model}: [green]OK[/] ({asin})")
                else:
                    # Search fallback
                    found = resolve_asin_via_search(
                        browser, prod, site, mp_config, config_path=project_path,
                    )
                    if found:
                        console.print(f"  {prod.model}: [yellow]Found[/] {found} (saved to config)")
                        found_count += 1
                    else:
                        console.print(f"  {prod.model}: [red]Not found[/]")

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


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path) as f:
        return sum(1 for _ in f) - 1  # Subtract header


if __name__ == "__main__":
    app()
