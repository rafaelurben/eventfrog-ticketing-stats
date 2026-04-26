"""
EventFrog Analytics Dashboard
Fetches event data from EventFrog API and generates interactive charts with Plotly.
"""

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import plotly.graph_objects as go
import requests
from dotenv import load_dotenv
from plotly.subplots import make_subplots

from swiss_coordinates import load_ch_ortsverzeichnis, resolve_city_coordinates

load_dotenv()

PARENT_DIR = Path(__file__).parent
CONFIG_DIR = PARENT_DIR / "config"
TEMPLATE_DIR = PARENT_DIR / "templates"
HTML_DIR = PARENT_DIR / "html"
os.makedirs(HTML_DIR, exist_ok=True)
CSV_ORTSVERZEICHNIS_PATH = CONFIG_DIR / "ch_ortsverzeichnis.csv"

# Configuration
API_BASE_URL = "https://api.eventfrog.net"
API_KEY = os.getenv("EVENTFROG_API_KEY")

if not API_KEY:
    raise ValueError("EVENTFROG_API_KEY environment variable not set")


def read_json_file(path: Path):
    with open(path) as f_in:
        return json.load(f_in)


try:
    CATEGORY_CONFIG = read_json_file(CONFIG_DIR / "categories.json")
except Exception as e:
    print(f"Error reading categories.json: {e}")
    CATEGORY_CONFIG = {}

try:
    CH_ORTS_INDEX = load_ch_ortsverzeichnis(CSV_ORTSVERZEICHNIS_PATH)
    print(f"Loaded Swiss place directory with {sum(len(v) for v in CH_ORTS_INDEX.values())} entries")
except Exception as e:
    print(f"Error reading ch_ortsverzeichnis.csv: {e}")
    CH_ORTS_INDEX = {}

CATEGORY_NAME_MAPPINGS = CATEGORY_CONFIG.get("mappings", {})
CATEGORY_COLORS = CATEGORY_CONFIG.get("colors", {})

# HTTP client setup
SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
})


class EventFrogClient:
    """Client for EventFrog Organizer API."""

    def __init__(self, base_url: str, session: requests.Session):
        self.base_url = base_url
        self.session = session

    def _paginate(self, endpoint: str, params: Dict = None) -> List[Dict]:
        """Fetch all paginated results from an endpoint."""
        if params is None:
            params = {}

        all_data = []
        page = 1

        while True:
            params["page"] = page
            params["perPage"] = 1000

            response = self.session.get(f"{self.base_url}{endpoint}", params=params)
            response.raise_for_status()

            data = response.json()
            all_data.extend(data.get("data", []))

            total = data.get("totalNumberOfResources", 0)
            if len(all_data) >= total:
                break

            page += 1

        return all_data

    def get_events(self) -> List[Dict]:
        """Fetch all events."""
        return self._paginate("/organizer/v1/events")

    def get_event(self, event_id: int) -> Dict:
        """Fetch event details."""
        response = self.session.get(f"{self.base_url}/organizer/v1/events/{event_id}")
        response.raise_for_status()
        return response.json()

    def get_ticket_categories(self, event_id: int) -> List[Dict]:
        """Fetch ticket categories for an event."""
        return self._paginate(f"/organizer/v1/events/{event_id}/ticketcategories")

    def get_transactions(self, event_id: int) -> List[Dict]:
        """Fetch all transactions for an event."""
        return self._paginate(f"/organizer/v1/events/{event_id}/tickettransactions")


class TicketAnalyzer:
    """Analyzes ticket data and generates statistics."""

    def __init__(self, client: EventFrogClient):
        self.client = client
        self.events = []
        self.categories_by_event = {}
        self.transactions_by_event = {}
        self.color_map = {}  # Maps category_id to color
        self.category_name_to_color = {}  # Maps category name to color

    def fetch_all_data(self):
        """Fetch events, categories, and transactions."""
        print("Fetching events...")
        self.events = self.client.get_events()
        print(f"Found {len(self.events)} events")

        for event in self.events:
            event_id = event["id"]
            print(f"Fetching data for event {event_id}...")

            # Fetch categories
            self.categories_by_event[event_id] = self.client.get_ticket_categories(event_id)

            # Fetch transactions
            self.transactions_by_event[event_id] = self.client.get_transactions(event_id)

        self._assign_colors()

    def _get_category_title(self, category: Dict) -> str:
        """Extract category title from localized info and apply mappings."""
        localized = category.get("localizedInfo", [])
        if localized:
            title = localized[0].get("title", "Unknown")
        else:
            title = "Unknown"

        # Apply category name mappings
        return CATEGORY_NAME_MAPPINGS.get(title, title)

    def _assign_colors(self):
        """Assign hardcoded colors to categories."""
        # Build mapping from category ID to color AND category name to color
        for event_id, categories in self.categories_by_event.items():
            for cat in categories:
                cat_id = cat["id"]
                title = self._get_category_title(cat)

                # Look up color from hardcoded mapping using mapped title
                if title in CATEGORY_COLORS:
                    color = CATEGORY_COLORS[title]
                else:
                    # Default to gray for unmapped categories
                    color = "#808080"

                self.color_map[cat_id] = color
                self.category_name_to_color[title] = color
                print(f"Category ID {cat_id}: '{title}' -> Color: {color}")

    def _lookup_city_coordinates(self, postcode: str, city: str, country: str) -> Tuple[float, float] | None:
        """Resolve Swiss city coordinates from the local ortsverzeichnis."""
        coords = resolve_city_coordinates(CH_ORTS_INDEX, postcode, city, country)
        if coords:
            return coords
        else:
            print(f"Could not resolve coordinates for city: {city}, postcode: {postcode}, country: {country}")
            return None

    def get_category_name(self, event_id: int, category_id: int) -> str:
        """Get category name by event and category ID."""
        categories = self.categories_by_event.get(event_id, [])
        for cat in categories:
            if cat["id"] == category_id:
                return self._get_category_title(cat)
        return f"Category {category_id}"

    def analyze_tickets_by_category(self) -> Dict[int, Dict[str, Dict[str, int]]]:
        """
        Analyze tickets grouped by event, then by category.
        Returns: {event_id: {category_name: {'active': count, 'blocked': count}}}
        """
        result = {}

        for event_id, transactions in self.transactions_by_event.items():
            event_stats = defaultdict(lambda: {"active": 0, "blocked": 0})

            for transaction in transactions:
                # Skip cancelled transactions
                for ticket in transaction.get("tickets", []):
                    if ticket.get("cancelled", False):
                        continue

                    category_id = ticket.get("categoryId")
                    category_name = self.get_category_name(event_id, category_id)

                    # Determine if blocked
                    is_blocked = transaction.get("type") == "blocking"

                    if is_blocked:
                        event_stats[category_name]["blocked"] += 1
                    else:
                        event_stats[category_name]["active"] += 1

            result[event_id] = dict(event_stats)

        return result

    def analyze_ticket_sales_over_time(self) -> Dict[int, Dict[str, List[Tuple[str, int]]]]:
        """
        Analyze ticket sales over time per event (by orderDate).
        Excludes blocked transactions.
        Returns: {event_id: {category_name: [(date_str, count), ...]}}
        """
        result = {}

        for event_id, transactions in self.transactions_by_event.items():
            daily_sales = defaultdict(lambda: defaultdict(int))
            date_range_set = set()

            for transaction in transactions:
                # Skip blocking transactions
                if transaction.get("type") == "blocking":
                    continue

                order_date_str = transaction.get("orderDate", "")
                if not order_date_str:
                    continue

                # Parse date
                order_date = datetime.fromisoformat(order_date_str.replace("Z", "+00:00"))
                date_key = order_date.date()
                date_range_set.add(date_key)

                # Count tickets by category
                for ticket in transaction.get("tickets", []):
                    if ticket.get("cancelled", False):
                        continue

                    category_id = ticket.get("categoryId")
                    category_name = self.get_category_name(event_id, category_id)
                    daily_sales[date_key][category_name] += 1

            # Fill in missing days with zeros and create sorted output
            if date_range_set:
                min_date = min(date_range_set)
                max_date = max(date_range_set)

                sales_by_category = defaultdict(list)
                current_date = min_date

                while current_date <= max_date:
                    # Get sales for this date
                    for category in daily_sales[current_date]:
                        count = daily_sales[current_date][category]
                        sales_by_category[category].append((str(current_date), count))

                    # Also add zeros for categories with sales on other days
                    all_categories_in_event = set()
                    for date_sales in daily_sales.values():
                        all_categories_in_event.update(date_sales.keys())

                    for category in all_categories_in_event:
                        if category not in daily_sales[current_date]:
                            sales_by_category[category].append((str(current_date), 0))

                    current_date += timedelta(days=1)

                # Sort by date within each category
                for category in sales_by_category:
                    sales_by_category[category].sort(key=lambda x: x[0])

                result[event_id] = dict(sales_by_category)
            else:
                result[event_id] = {}

        return result

    def analyze_tickets_by_city(self) -> Dict[int, Dict]:
        """
        Analyze tickets grouped by city for active sales only.
        Returns: {event_id: {name: "Event name", date: "dd.mm.yyyy", cities: [{postcode, city, country, ticketCount, lat, lng}, ...]}}
        """
        result = {}

        for event_id, transactions in self.transactions_by_event.items():
            # Get event name and date
            event_name = f"Event {event_id}"
            event_date = ""
            for event in self.events:
                if event["id"] == event_id:
                    localized = event.get("localizedInfo", [])
                    if localized:
                        event_name = localized[0].get("title", f"Event {event_id}")

                    # Extract and format date
                    begin_date = event.get("beginDate", "")
                    if begin_date:
                        try:
                            event_date_obj = datetime.fromisoformat(begin_date.replace("Z", "+00:00"))
                            event_date = event_date_obj.strftime("%d.%m.%Y")
                        except:
                            event_date = ""
                    break

            # Aggregate tickets by city
            city_stats = defaultdict(lambda: {"postcode": None, "city": None, "country": None, "ticketCount": 0})

            for transaction in transactions:
                # Skip blocking transactions - only include active sales
                if transaction.get("type") == "blocking":
                    continue

                # Get customer info
                customer = transaction.get("customer")
                if not customer:
                    continue

                city = customer.get("city")
                postcode = customer.get("zip")
                country = customer.get("country")

                # Skip if no city info
                if not city:
                    continue

                # Create city key (postcode_city_country)
                city_key = (postcode, city, country)

                # Count tickets
                for ticket in transaction.get("tickets", []):
                    if ticket.get("cancelled", False):
                        continue
                    city_stats[city_key]["ticketCount"] += 1
                    city_stats[city_key]["postcode"] = postcode
                    city_stats[city_key]["city"] = city
                    city_stats[city_key]["country"] = country

            # Convert to list format and resolve coordinates from the Swiss ortsverzeichnis
            cities_list = []
            for stats in city_stats.values():
                coordinates = self._lookup_city_coordinates(stats["postcode"], stats["city"], stats["country"])
                city_obj = {
                    "postcode": stats["postcode"],
                    "city": stats["city"],
                    "country": stats["country"],
                    "ticketCount": stats["ticketCount"],
                    "lat": coordinates[0] if coordinates else None,
                    "lng": coordinates[1] if coordinates else None,
                }

                # Only include cities with valid coordinates
                if city_obj["lat"] is not None and city_obj["lng"] is not None:
                    cities_list.append(city_obj)

            result[event_id] = {
                "name": event_name,
                "date": event_date,
                "cities": cities_list
            }

        return result


class ChartGenerator:
    """Generates interactive Plotly charts."""

    def __init__(self, analyzer: TicketAnalyzer):
        self.analyzer = analyzer

    def generate_horizontal_stacked_bar(self, output_file: str = "ticket_summary.html"):
        """Generate horizontal stacked bar chart with categories and blocked tickets."""
        ticket_stats = self.analyzer.analyze_tickets_by_category()

        # Build events data map with dates
        events_data = {}
        for event in self.analyzer.events:
            event_id = event["id"]
            localized = event.get("localizedInfo", [])
            title = localized[0].get("title", f"Event {event_id}") if localized else f"Event {event_id}"

            # Add date (dd.mm.yyyy format) using beginDate
            begin_date = event.get("beginDate", "")
            if begin_date:
                try:
                    event_date = datetime.fromisoformat(begin_date.replace("Z", "+00:00"))
                    date_str = event_date.strftime("%d.%m.%Y")
                    title = f"{title} ({date_str})"
                except:
                    pass

            events_data[event_id] = title

        # Collect all unique categories across all events
        all_categories = set()
        for event_id, stats in ticket_stats.items():
            all_categories.update(stats.keys())
        all_categories = sorted(all_categories)

        # Prepare data for chart
        event_names = []
        traces_data = {cat: {"active": [], "blocked": []} for cat in all_categories}

        for event_id in sorted(ticket_stats.keys()):
            event_name = events_data[event_id]
            event_names.append(event_name)
            stats = ticket_stats[event_id]

            for category in all_categories:
                category_stats = stats.get(category, {"active": 0, "blocked": 0})
                traces_data[category]["active"].append(category_stats["active"])
                traces_data[category]["blocked"].append(category_stats["blocked"])

        # Create figure
        fig = go.Figure()

        # Track which categories have been added to legend
        added_to_legend = set()

        # First pass: Add all ACTIVE ticket traces
        for category in all_categories:
            # Get color directly from category name mapping
            color = self.analyzer.category_name_to_color.get(category, "#808080")

            # Active tickets (only show once in legend)
            show_legend = category not in added_to_legend
            fig.add_trace(go.Bar(
                y=event_names,
                x=traces_data[category]["active"],
                name=category,
                orientation="h",
                marker={"color": color},
                hovertemplate=f"{category}: %{{x}}<extra></extra>",
                showlegend=show_legend
            ))

            if show_legend:
                added_to_legend.add(category)

        # Second pass: Add all BLOCKED ticket traces (appears rightmost)
        for category in all_categories:
            # Blocked tickets (with black/white striped pattern) - don't show separately in legend
            if any(traces_data[category]["blocked"]):
                fig.add_trace(go.Bar(
                    y=event_names,
                    x=traces_data[category]["blocked"],
                    name=f"{category} (Blocked)",
                    orientation="h",
                    marker={
                        "color": "rgba(0, 0, 0, 0.5)",  # Semi-transparent black
                        "pattern_shape": "/",
                        "pattern_size": 5,
                        "pattern_fillmode": "replace"
                    },
                    hovertemplate=f"{category} (Blocked): %{{x}}<extra></extra>",
                    showlegend=False
                ))

        fig.update_layout(
            barmode="stack",
            title="Ticket Summary by Event and Category",
            xaxis_title="Number of Tickets",
            xaxis={"range": [0, 500]},
            yaxis_title="Event",
            hovermode="closest",
            height=400 + len(events_data) * 50,
            showlegend=True
        )

        output_path = HTML_DIR / output_file
        fig.write_html(output_path)
        print(f"Saved horizontal stacked bar chart to {output_path}")

    def generate_sales_over_time_charts(self, output_file: str = "sales_timeline.html"):
        """Generate stacked area charts for each event with cumulative values."""
        sales_data = self.analyzer.analyze_ticket_sales_over_time()

        # Filter events with sales data and add dates
        events_with_sales = []
        for event in self.analyzer.events:
            event_id = event["id"]
            if event_id in sales_data and sales_data[event_id]:
                localized = event.get("localizedInfo", [])
                title = localized[0].get("title", f"Event {event_id}") if localized else f"Event {event_id}"

                # Add date (dd.mm.yyyy format) using beginDate
                begin_date = event.get("beginDate", "")
                if begin_date:
                    try:
                        event_date = datetime.fromisoformat(begin_date.replace("Z", "+00:00"))
                        date_str = event_date.strftime("%d.%m.%Y")
                        title = f"{title} ({date_str})"
                    except:
                        pass

                events_with_sales.append((event_id, title))

        if not events_with_sales:
            print("No sales data available for timeline charts")
            return

        num_events = len(events_with_sales)

        # Create subplots
        fig = make_subplots(
            rows=num_events, cols=1,
            subplot_titles=[title for _, title in events_with_sales],
            specs=[[{"secondary_y": False}] for _ in range(num_events)],
            vertical_spacing=0.12
        )

        # Track which categories have been added to legend globally
        added_to_legend = set()

        for row_idx, (event_id, event_title) in enumerate(events_with_sales, 1):
            sales_by_cat = sales_data[event_id]

            if not sales_by_cat:
                continue

            # Get all unique dates across all categories for this event
            all_dates = set()
            for dates_and_counts in sales_by_cat.values():
                for date_str, _ in dates_and_counts:
                    all_dates.add(date_str)

            all_dates_sorted = sorted(all_dates)

            # For each category, build daily sales data with zeros for missing dates
            category_daily_sales = {}
            for category in sorted(sales_by_cat.keys()):
                dates_and_counts = sales_by_cat[category]
                sales_dict = {d: c for d, c in dates_and_counts}

                daily_values = []
                for date_str in all_dates_sorted:
                    daily_values.append(sales_dict.get(date_str, 0))

                category_daily_sales[category] = daily_values

            # Build cumulative values - each category shows cumulative total across all dates
            category_cumulative_sales = {}

            for category in sorted(sales_by_cat.keys()):
                cumulative_values = []
                cumsum = 0
                for daily_count in category_daily_sales[category]:
                    cumsum += daily_count
                    cumulative_values.append(cumsum)
                category_cumulative_sales[category] = cumulative_values

            # Add traces for each category with stackgroup for area stacking
            # Use event_id in stackgroup to ensure proper stacking per event
            for category in sorted(sales_by_cat.keys()):
                # Get color directly from category name mapping
                color = self.analyzer.category_name_to_color.get(category, "#808080")

                # Only show in legend once globally
                show_legend = category not in added_to_legend
                if show_legend:
                    added_to_legend.add(category)

                fig.add_trace(
                    go.Scatter(
                        x=all_dates_sorted,
                        y=category_cumulative_sales[category],
                        name=category,
                        mode="lines",
                        stackgroup=f"group{row_idx}",  # Use row index for stacking per event
                        legendgroup=category,  # Link legend items across all subplots
                        fillcolor=color,
                        line={"color": color, "width": 0.5},
                        hovertemplate=f"{category}: %{{y}}<extra></extra>",
                        showlegend=show_legend
                    ),
                    row=row_idx, col=1
                )

            fig.update_xaxes(title_text="Date", row=row_idx, col=1)
            fig.update_yaxes(title_text="Cumulative Tickets", row=row_idx, col=1)

        fig.update_layout(
            title_text="Ticket Sales Over Time by Event",
            height=300 * num_events,
            hovermode="x unified",
            showlegend=True
        )

        output_path = HTML_DIR / output_file
        fig.write_html(output_path)
        print(f"Saved sales timeline charts to {output_path}")

    def generate_city_map_chart(self, output_file: str = "city_map.html"):
        """Generate interactive city map with event checkboxes and ticker count visualization."""
        city_data = self.analyzer.analyze_tickets_by_city()

        # Filter out events with no city data
        city_data = {event_id: data for event_id, data in city_data.items() if data["cities"]}

        if not city_data:
            print("No city data available for map")
            sys.exit(1)

        # Read template
        template_path = TEMPLATE_DIR / "city_map.html"
        if not template_path.exists():
            print(f"Template not found: {template_path}")
            return

        template_content = template_path.read_text(encoding="utf-8")

        # Convert city data to JSON string
        city_data_json = json.dumps(city_data)

        # Replace placeholder with actual data
        updated_html = template_content.replace("<!-- CITY_DATA_PLACEHOLDER -->", f"const cityData = {city_data_json};")

        # Write output file
        output_path = HTML_DIR / output_file
        output_path.write_text(updated_html, encoding="utf-8")
        print(f"Saved city map to {output_path}")

    def generate_index_file(self):
        source_index_path = TEMPLATE_DIR / "index.html"
        target_index_path = HTML_DIR / "index.html"

        html = source_index_path.read_text(encoding="utf-8")

        now_str = datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")

        pattern = re.compile(
            r'(<span\s+id="last-update">)(.*?)(</span>)',
            flags=re.IGNORECASE | re.DOTALL,
        )

        updated_html, count = pattern.subn(rf"\1{now_str}\3", html, count=1)
        if count == 0:
            raise RuntimeError('No <span id="last-update">...</span> found in index.html')

        target_index_path.write_text(updated_html, encoding="utf-8")
        print(f"Updated {target_index_path.name}: {now_str}")


def main():
    """Main entry point."""
    print("EventFrog Analytics Dashboard")
    print("=" * 50)

    # Initialize client and analyzer
    client = EventFrogClient(API_BASE_URL, SESSION)
    analyzer = TicketAnalyzer(client)

    # Fetch data
    analyzer.fetch_all_data()

    # Generate charts
    chart_gen = ChartGenerator(analyzer)
    chart_gen.generate_horizontal_stacked_bar("ticket_summary.html")
    chart_gen.generate_sales_over_time_charts("sales_timeline.html")
    chart_gen.generate_city_map_chart("city_map.html")
    chart_gen.generate_index_file()

    print("\nDone! Generated files:")
    print("  - html/index.html")
    print("  - html/ticket_summary.html")
    print("  - html/sales_timeline.html")
    print("  - html/city_map.html")


if __name__ == "__main__":
    main()
else:
    print("Module imported - no main execution")
