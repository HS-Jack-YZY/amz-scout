"""CLI interface for amz-scout."""

import json as json_mod
import logging
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
from amz_scout.db import (
    import_from_csv,
    import_from_raw_json,
    open_db,
    resolve_db_path,
    upsert_competitive,
)
from amz_scout.marketplace import setup_marketplace
from amz_scout.models import CompetitiveData, Product
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

    db_path = resolve_db_path(proj.project.output_dir)
    with open_db(db_path) as db_conn:
        # ── Price history (Keepa) ──
        if not data_only:
            _scrape_price_history(
                proj, products, target_sites, marketplaces, output_base, headed_mode,
                detailed=detailed, db_conn=db_conn,
            )

        # ── Amazon product pages (browser needed) ──
        if not history_only:
            if not check_browser_use_installed():
                console.print("[red]browser-use CLI not found.[/] Install: uv tool install browser-use")
                raise typer.Exit(1)

            _scrape_amazon(
                proj, products, target_sites, marketplaces,
                output_base, project_path, headed_mode, db_conn=db_conn,
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
    db_conn=None,
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
            # DB write (fire-and-forget)
            if db_conn:
                _store_raw_to_db(db_conn, raw_dir, products, site)

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
    db_conn=None,
) -> None:
    """Scrape Amazon product pages across target marketplaces."""
    console.print("\n[bold]── Amazon Product Pages ──[/]")

    for site in target_sites:
        mp_config = marketplaces.get(site)
        if not mp_config:
            continue

        console.print(f"\n[bold cyan]  {site} ({mp_config.amazon_domain})[/]")
        browser = BrowserSession(headed=headed, session=f"amz-scout-{site.lower()}")

        results: list[CompetitiveData] = []
        try:
            setup_marketplace(browser, site, mp_config)

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
            _save_competitive(
                results, output_base, mp_config, site,
                db_conn=db_conn, project=proj.project.name,
            )
            has_price = sum(1 for r in results if r.price not in ("N/A", "", "Currently unavailable"))
            console.print(f"  [green]{site}: {has_price}/{len(results)} with price[/]")

            # Data validation
            _validate_results(results, site)

        except Exception as e:
            # Save whatever we have so far on unexpected crash
            if results:
                _save_competitive(
                    results, output_base, mp_config, site,
                    db_conn=db_conn, project=proj.project.name,
                )
                console.print(f"  [yellow]{site}: saved {len(results)} partial results before error[/]")
            console.print(f"  [red]{site}: {e}[/]")

        finally:
            browser.close()


def _save_competitive(
    results: list[CompetitiveData],
    output_base: Path,
    mp_config: MarketplaceConfig,
    site: str,
    db_conn=None,
    project: str = "",
) -> None:
    """Save competitive data CSV, merging with existing data if present."""
    data_dir = output_base / "data" / mp_config.region
    csv_path = data_dir / f"{site.lower()}_competitive_data.csv"
    merged = merge_competitive(read_competitive_data(csv_path), results)
    write_competitive_data(merged, csv_path)
    # DB write (fire-and-forget)
    if db_conn:
        _store_competitive_to_db(db_conn, results, project=project)


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

    console.print("[green]Config valid![/]")
    console.print(f"  Project: {proj.project.name}")
    console.print(f"  Markets: {', '.join(proj.target_marketplaces)}")
    console.print(f"  Products: {len(proj.products)}")


def _render_db_stats(stats: dict, title: str = "Database", show_meta: bool = True) -> None:
    """Render DB table counts as a Rich table."""
    db_table = Table(title=title)
    db_table.add_column("Table")
    db_table.add_column("Rows", justify="right")
    for key, val in stats.items():
        if key in ("date_range", "distinct_products", "distinct_sites"):
            continue
        db_table.add_row(key, f"{val:,}")
    console.print(db_table)
    if show_meta:
        console.print(f"  Date range: {stats['date_range']}")
        console.print(f"  Distinct products: {stats['distinct_products']}")
        console.print(f"  Distinct sites: {stats['distinct_sites']}")


@app.command()
def status(
    project_config: str = typer.Argument(help="Path to project YAML config"),
) -> None:
    """Check data completeness: CSV files, database, and Keepa freshness."""
    project_path, mp_path = _resolve_config_paths(project_config)
    proj = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)
    output_base = Path(proj.project.output_dir)
    products = [p.to_product() for p in proj.products]

    # ── CSV status ──
    csv_table = Table(title="CSV Files")
    csv_table.add_column("Site")
    csv_table.add_column("Competitive")
    csv_table.add_column("Price History")

    for site in proj.target_marketplaces:
        mp = marketplaces.get(site)
        if not mp:
            continue
        data_dir = output_base / "data" / mp.region
        sl = site.lower()
        comp_file = data_dir / f"{sl}_competitive_data.csv"
        hist_file = data_dir / f"{sl}_price_history.csv"
        comp_n = _count_lines(comp_file)
        hist_n = _count_lines(hist_file)
        comp_s = f"[green]{comp_n} rows[/]" if comp_file.exists() else "[red]missing[/]"
        hist_s = f"[green]{hist_n} rows[/]" if hist_file.exists() else "[red]missing[/]"
        csv_table.add_row(site, comp_s, hist_s)

    console.print(csv_table)

    # ── DB status ──
    db_path = resolve_db_path(proj.project.output_dir)
    if db_path.exists():
        from amz_scout.db import query_stats
        from amz_scout.freshness import (
            FreshnessStrategy,
            evaluate_freshness,
            format_freshness_matrix,
            query_freshness,
        )
        with open_db(db_path) as conn:
            stats = query_stats(conn)
            fetched_map = query_freshness(conn, products, proj.target_marketplaces)
            fresh_results = evaluate_freshness(
                products, proj.target_marketplaces, fetched_map,
                FreshnessStrategy.MAX_AGE,
            )
            fresh_rows = format_freshness_matrix(fresh_results, proj.target_marketplaces)

        _render_db_stats(stats, title=f"Database ({db_path})")

        if fresh_rows:
            fresh_table = Table(title="Keepa Data Freshness")
            fresh_table.add_column("Brand")
            fresh_table.add_column("Model")
            for s in proj.target_marketplaces:
                fresh_table.add_column(s, justify="center")
            for row in fresh_rows:
                fresh_table.add_row(
                    row.get("brand", ""),
                    row.get("model", ""),
                    *[row.get(s, "-") for s in proj.target_marketplaces],
                )
            console.print(fresh_table)
    else:
        console.print(f"[yellow]Database not found:[/] {db_path}")


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


def _store_raw_to_db(db_conn, raw_dir: Path, products: list[Product], site: str) -> None:
    """Import Keepa raw JSON into DB. Failures are logged, not raised."""
    ok, fail = import_from_raw_json(db_conn, raw_dir, products, site)
    if fail:
        console.print(f"  [yellow]DB: {ok} stored, {fail} failed[/]")


def _store_competitive_to_db(
    db_conn, results: list[CompetitiveData], project: str = "",
) -> None:
    """Write browser snapshots to DB. Failures are logged, not raised."""
    try:
        upsert_competitive(db_conn, results, project=project)
    except Exception:
        logging.getLogger(__name__).exception("DB write failed for competitive data")


# ─── Admin subcommand group (one-time operations) ────────────────────

admin_app = typer.Typer(name="admin", help="One-time admin operations (migrate, merge, reparse)")
app.add_typer(admin_app)


@admin_app.command()
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

    from amz_scout.scraper.keepa import _empty_history, _parse_product

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

    # Also rebuild DB from raw JSON
    db_path = resolve_db_path(proj.project.output_dir)
    with open_db(db_path) as db_conn:
        for site in target_sites:
            mp_config = marketplaces.get(site)
            if not mp_config:
                continue
            raw_dir = output_base / "data" / mp_config.region / "raw"
            if raw_dir.exists():
                _store_raw_to_db(db_conn, raw_dir, products, site)

    console.print("[green bold]Done![/]")


@admin_app.command()
def migrate(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Import existing raw JSON + CSV data into SQLite database."""
    _setup_logging(verbose)
    project_path, mp_path = _resolve_config_paths(project_config)
    proj = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)
    products = [p.to_product() for p in proj.products]
    output_base = Path(proj.project.output_dir)
    db_path = resolve_db_path(proj.project.output_dir)

    console.print("[bold]── Migrate to SQLite ──[/]")
    console.print(f"  DB: {db_path}")

    total_keepa, total_competitive = 0, 0

    with open_db(db_path) as db_conn:
        for site in proj.target_marketplaces:
            mp_config = marketplaces.get(site)
            if not mp_config:
                continue

            # Import Keepa raw JSON
            raw_dir = output_base / "data" / mp_config.region / "raw"
            if raw_dir.exists():
                ok, fail = import_from_raw_json(db_conn, raw_dir, products, site)
                total_keepa += ok
                status = f"[green]{ok} products[/]"
                if fail:
                    status += f" [yellow]({fail} failed)[/]"
                console.print(f"  [cyan]{site}[/] Keepa: {status}")

            # Import competitive CSV
            data_dir = output_base / "data" / mp_config.region
            csv_path = data_dir / f"{site.lower()}_competitive_data.csv"
            if csv_path.exists():
                count = import_from_csv(db_conn, csv_path)
                total_competitive += count
                console.print(f"  [cyan]{site}[/] Competitive: [green]{count} rows[/]")

        from amz_scout.db import query_stats
        stats = query_stats(db_conn)

    console.print("\n[green bold]Migration complete![/]")
    console.print(f"  Keepa products: {total_keepa}")
    console.print(f"  Competitive rows: {total_competitive}")
    console.print(f"  Time series data points: {stats.get('keepa_time_series', 0)}")


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path) as f:
        return sum(1 for _ in f) - 1  # Subtract header


# ─── Query subcommand group ───────────────────────────────────────────

query_app = typer.Typer(name="query", help="Query the SQLite database")
app.add_typer(query_app)

from amz_scout.api import (  # noqa: E402
    query_availability,
    query_compare,
    query_deals,
    query_latest,
    query_ranking,
    query_sellers,
    query_trends,
)


def _check_result(result: dict) -> None:
    """Exit with error message if the API result indicates failure."""
    if not result["ok"]:
        console.print(f"[red]{result['error']}[/]")
        raise typer.Exit(1)


def _render_output(rows: list[dict], fmt: str, columns: list[str] | None = None) -> None:
    """Render query results as table, csv, or json."""
    if not rows:
        console.print("[yellow]No data found.[/]")
        return

    if fmt == "json":
        console.print(json_mod.dumps(rows, indent=2, ensure_ascii=False, default=str))
        return

    if fmt == "csv":
        import csv
        import io
        cols = columns or list(rows[0].keys())
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(cols)
        for row in rows:
            writer.writerow([row.get(c, "") for c in cols])
        print(buf.getvalue(), end="")
        return

    # Default: Rich table
    cols = columns or list(rows[0].keys())
    table = Table()
    for c in cols:
        table.add_column(c)
    for row in rows:
        table.add_row(*[str(row.get(c, "")) for c in cols])
    console.print(table)


@query_app.command("latest")
def query_latest_cmd(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    marketplace: str | None = typer.Option(None, "-m", "--marketplace"),
    category: str | None = typer.Option(None, "-c", "--category"),
    fmt: str = typer.Option("table", "--format", help="Output format: table|csv|json"),
) -> None:
    """Show latest competitive data per product."""
    result = query_latest(project_config, marketplace=marketplace, category=category)
    _check_result(result)
    cols = ["site", "brand", "model", "price_cents", "currency", "rating",
            "review_count", "bsr", "available", "fulfillment"]
    _render_output(result["data"], fmt, cols)


@query_app.command("trends")
def query_trends_cmd(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    product: str = typer.Option(..., "-p", "--product", help="Product model or ASIN"),
    marketplace: str = typer.Option("UK", "-m", "--marketplace"),
    days: int = typer.Option(90, "--days"),
    series: str = typer.Option(
        "new", "--series",
        help="Series: amazon|new|used|sales_rank|rating|reviews",
    ),
    fmt: str = typer.Option("table", "--format", help="Output format: table|csv|json"),
) -> None:
    """Show price/data trends for a product over time."""
    result = query_trends(
        project_config, product=product, marketplace=marketplace,
        series=series, days=days,
    )
    _check_result(result)
    meta = result["meta"]
    console.print(
        f"[bold]{meta.get('asin', '')} / {marketplace} / "
        f"{meta.get('series_name', '')} (last {days} days)[/]"
    )
    _render_output(result["data"], fmt, ["date", "value"])


@query_app.command("compare")
def query_compare_cmd(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    product: str = typer.Option(..., "-p", "--product", help="Product model substring"),
    fmt: str = typer.Option("table", "--format", help="Output format: table|csv|json"),
) -> None:
    """Compare one product across all marketplaces."""
    result = query_compare(project_config, product=product)
    _check_result(result)
    cols = ["site", "brand", "model", "price_cents", "currency", "rating",
            "review_count", "bsr", "available"]
    _render_output(result["data"], fmt, cols)


@query_app.command("ranking")
def query_ranking_cmd(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    marketplace: str = typer.Option(..., "-m", "--marketplace"),
    category: str | None = typer.Option(None, "-c", "--category"),
    fmt: str = typer.Option("table", "--format", help="Output format: table|csv|json"),
) -> None:
    """Products ranked by BSR for a marketplace."""
    result = query_ranking(project_config, marketplace=marketplace, category=category)
    _check_result(result)
    cols = ["bsr", "brand", "model", "price_cents", "currency", "rating",
            "review_count"]
    _render_output(result["data"], fmt, cols)


@query_app.command("availability")
def query_availability_cmd(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    fmt: str = typer.Option("table", "--format", help="Output format: table|csv|json"),
) -> None:
    """Availability matrix: all products across all sites."""
    result = query_availability(project_config)
    _check_result(result)
    _render_output(result["data"], fmt)


@query_app.command("sellers")
def query_sellers_cmd(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    product: str = typer.Option(..., "-p", "--product", help="Product model or ASIN"),
    marketplace: str = typer.Option("UK", "-m", "--marketplace"),
    fmt: str = typer.Option("table", "--format", help="Output format: table|csv|json"),
) -> None:
    """Buy Box seller history for a product."""
    result = query_sellers(project_config, product=product, marketplace=marketplace)
    _check_result(result)
    meta = result["meta"]
    console.print(f"[bold]{meta.get('asin', '')} / {marketplace} / Buy Box History[/]")
    _render_output(result["data"], fmt, ["date", "seller_id"])


@query_app.command("deals")
def query_deals_cmd(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    marketplace: str | None = typer.Option(None, "-m", "--marketplace"),
    fmt: str = typer.Option("table", "--format", help="Output format: table|csv|json"),
) -> None:
    """Deal/promotion history."""
    result = query_deals(project_config, marketplace=marketplace)
    _check_result(result)
    _render_output(result["data"], fmt)



@admin_app.command("merge-dbs")
def merge_dbs(
    output_dir: str = typer.Option("output", help="Root output directory to scan"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without merging"),
) -> None:
    """Merge per-project SQLite databases into a single shared database."""
    root = Path(output_dir)
    shared_path = root / "amz_scout.db"

    # Find per-project DBs
    project_dbs = sorted(root.glob("*/amz_scout.db"))
    if not project_dbs:
        console.print("[yellow]No per-project databases found.[/]")
        return

    console.print("[bold]── Merge Databases ──[/]")
    console.print(f"  Target: {shared_path}")
    console.print(f"  Sources: {len(project_dbs)}")

    for db in project_dbs:
        project_name = db.parent.name
        console.print(f"  - {db} (project: {project_name})")

    if dry_run:
        console.print("\n[yellow]Dry run — no changes made.[/]")
        return

    tables_keepa = [
        "keepa_products", "keepa_time_series",
        "keepa_buybox_history", "keepa_coupon_history", "keepa_deals",
    ]

    with open_db(shared_path) as conn:
        for db_file in project_dbs:
            if db_file.resolve() == shared_path.resolve():
                continue
            project_name = db_file.parent.name
            console.print(f"\n  Merging [cyan]{project_name}[/]...")

            conn.execute("ATTACH DATABASE ? AS src", (str(db_file),))
            try:
                for tbl in tables_keepa:
                    exists = conn.execute(
                        "SELECT 1 FROM src.sqlite_master WHERE type='table' AND name=?",
                        (tbl,),
                    ).fetchone()
                    if not exists:
                        continue

                    if tbl == "keepa_products":
                        conn.execute(f"INSERT OR REPLACE INTO {tbl} SELECT * FROM src.{tbl}")
                    else:
                        conn.execute(f"INSERT OR IGNORE INTO {tbl} SELECT * FROM src.{tbl}")

                    count = conn.execute(f"SELECT COUNT(*) FROM src.{tbl}").fetchone()[0]
                    console.print(f"    {tbl}: {count} rows")

                # Merge competitive_snapshots with project tag
                exists = conn.execute(
                    "SELECT 1 FROM src.sqlite_master WHERE type='table' AND name='competitive_snapshots'"
                ).fetchone()
                if exists:
                    src_cols = [
                        r["name"] for r in conn.execute("PRAGMA src.table_info(competitive_snapshots)")
                    ]
                    if "project" in src_cols:
                        conn.execute(
                            "INSERT OR IGNORE INTO competitive_snapshots "
                            "SELECT * FROM src.competitive_snapshots"
                        )
                    else:
                        # Source DB is v1 — inject project name
                        col_list = ", ".join(c for c in src_cols if c != "id")
                        conn.execute(
                            f"INSERT OR IGNORE INTO competitive_snapshots "
                            f"({col_list}, project) "
                            f"SELECT {col_list}, ? FROM src.competitive_snapshots",
                            (project_name,),
                        )
                    count = conn.execute(
                        "SELECT COUNT(*) FROM src.competitive_snapshots"
                    ).fetchone()[0]
                    console.print(f"    competitive_snapshots: {count} rows")

                conn.commit()
            finally:
                conn.execute("DETACH DATABASE src")

        # Summary
        from amz_scout.db import query_stats
        stats = query_stats(conn)

    console.print("\n[green bold]Merge complete![/]")
    _render_db_stats(stats, title="Shared Database", show_meta=False)


# ─── Keepa command (top-level) ───────────────────────────────────────


@app.command()
def keepa(
    project_config: str = typer.Argument(help="Path to project YAML config"),
    marketplace: str | None = typer.Option(None, "-m", "--marketplace"),
    product: str | None = typer.Option(None, "-p", "--product"),
    lazy: bool = typer.Option(False, "--lazy",
        help="Use cache no matter how old; fetch only if missing"),
    offline: bool = typer.Option(False, "--offline",
        help="DB only; skip if missing (zero API calls). Also regenerates CSV from cache."),
    max_age: int | None = typer.Option(None, "--max-age",
        help="Max cache age in days (default 7)"),
    fresh: bool = typer.Option(False, "--fresh",
        help="Always re-fetch from Keepa API"),
    check: bool = typer.Option(False, "--check",
        help="Show data freshness matrix only (no fetch)"),
    budget: bool = typer.Option(False, "--budget",
        help="Show Keepa API token balance only"),
    detailed: bool = typer.Option(False, "--detailed",
        help="Keepa detailed mode (~6 tok/product)"),
    fmt: str = typer.Option("table", "--format",
        help="Output format: table|csv|json"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    """Smart Keepa data fetch with cache-first freshness control.

    Default: --max-age 7 (use cache if <7 days old, re-fetch if older).
    Writes raw JSON + CSV + SQLite on every run.
    """
    _setup_logging(verbose)

    # ── Budget mode: no project config needed ──
    if budget:
        try:
            kc = KeepaClient()
        except ValueError:
            console.print("[red]Keepa API key not configured.[/]")
            console.print("Set KEEPA_API_KEY in .env or environment.")
            raise typer.Exit(1)
        tokens = kc.tokens_left
        console.print(f"  Tokens available: [bold]{tokens}[/] / 60")
        console.print("  Refill rate: 1 token/min")
        if tokens < 60:
            console.print(f"  Full refill in: ~{60 - tokens} min")
        return

    # ── Load config ──
    project_path, mp_path = _resolve_config_paths(project_config)
    proj = load_project_config(project_path)
    marketplaces = load_marketplace_config(mp_path)
    products = [p.to_product() for p in proj.products]
    target_sites = [marketplace] if marketplace else proj.target_marketplaces

    if product:
        products = [p for p in products if product.lower() in p.model.lower()]
        if not products:
            console.print(f"[red]No product matching:[/] {product}")
            raise typer.Exit(1)

    # ── Check mode: show freshness matrix, no fetch ──
    if check:
        from amz_scout.freshness import (
            FreshnessStrategy,
            evaluate_freshness,
            format_freshness_matrix,
            query_freshness,
        )
        db_path = resolve_db_path(proj.project.output_dir)
        with open_db(db_path) as conn:
            fetched_map = query_freshness(conn, products, target_sites)
            results = evaluate_freshness(
                products, target_sites, fetched_map, FreshnessStrategy.MAX_AGE
            )
            rows = format_freshness_matrix(results, target_sites)
        cols = ["brand", "model"] + target_sites
        _render_output(rows, fmt, cols)
        return

    # ── Fetch mode: cache-first data retrieval ──
    from amz_scout.freshness import resolve_strategy
    from amz_scout.keepa_service import get_keepa_data

    try:
        strategy, age_days = resolve_strategy(lazy, offline, max_age, fresh)
    except ValueError as e:
        console.print(f"[red]Error:[/] {e}")
        raise typer.Exit(1)

    output_base = Path(proj.project.output_dir)
    db_path = resolve_db_path(proj.project.output_dir)

    with open_db(db_path) as conn:
        result = get_keepa_data(
            conn, products, target_sites, marketplaces,
            strategy=strategy, max_age_days=age_days,
            detailed=detailed, output_base=output_base,
            on_progress=lambda msg: console.print(msg),
        )

    # Display results
    rows = []
    for o in result.outcomes:
        row: dict = {"site": o.site, "model": o.model, "source": o.source}
        if o.freshness.age_days is not None:
            row["age"] = f"{o.freshness.age_days}d"
        else:
            row["age"] = "-"
        if o.price_history:
            h = o.price_history
            row["buybox"] = h.buybox_current or ""
            row["amazon"] = h.amz_current or ""
            row["new"] = h.new_current or ""
            row["sales_rank"] = h.sales_rank or ""
            row["monthly_sold"] = h.monthly_sold or ""
        rows.append(row)

    cols = ["site", "model", "source", "age", "buybox", "amazon", "new",
            "sales_rank", "monthly_sold"]
    _render_output(rows, fmt, cols)

    console.print(
        f"\n  [dim]{result.cache_count} cached, {result.fetch_count} fetched, "
        f"{result.skip_count} skipped[/]"
    )
    if result.tokens_used > 0:
        console.print(
            f"  [dim]Tokens: {result.tokens_used} used, "
            f"{result.tokens_remaining} remaining[/]"
        )


if __name__ == "__main__":
    app()
