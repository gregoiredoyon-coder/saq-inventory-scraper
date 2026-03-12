"""
SAQ Inventory Scraper - Web UI
"""

import asyncio
import streamlit as st
import pandas as pd
from datetime import datetime

from saq_scraper import SAQScraper, filter_inventory


st.set_page_config(
    page_title="SAQ Inventory Scraper",
    page_icon="🍷",
    layout="wide",
)

st.markdown("""
<style>
    .product-card {
        background: linear-gradient(135deg, #7E003F 0%, #5a002d 100%);
        padding: 20px;
        border-radius: 10px;
        color: white;
        margin-bottom: 20px;
    }
    .product-name { font-size: 1.5em; font-weight: bold; }
    .product-price { font-size: 2em; font-weight: bold; color: #FFD700; }
</style>
""", unsafe_allow_html=True)


def get_stock_icon(qty: int) -> str:
    if qty == 0:
        return "🔴"
    elif qty < 5:
        return "🟡"
    return "🟢"


def run_scraper(url: str, load_all: bool = False):
    """Run the scraper."""
    async def _scrape():
        async with SAQScraper(headless=True) as scraper:
            return await scraper.scrape(url, load_all=load_all)
    return asyncio.run(_scrape())


def do_search(url: str, load_all: bool):
    """Execute search and store results."""
    if not url.startswith("http"):
        url = f"https://www.saq.com/fr/{url}"

    product, inventory = run_scraper(url, load_all=load_all)

    st.session_state.results = {
        "product": product,
        "inventory": inventory,
        "scraped_at": datetime.now()
    }


# Initialize session state
if "results" not in st.session_state:
    st.session_state.results = None

# Header
st.title("🍷 SAQ Inventory Scraper")
st.markdown("Check product availability across SAQ stores")

# Sidebar
with st.sidebar:
    st.header("Options")
    load_all = st.checkbox("Load ALL stores (~400)", value=False)
    location_filter = st.text_input("Filter by store name", placeholder="Beaubien, Laurier...")

    if st.button("Clear Results"):
        st.session_state.results = None
        st.rerun()

    st.markdown("---")
    st.markdown("**Tips:**")
    st.markdown("- Montreal stores use neighborhood names")
    st.markdown("- Loading all stores takes longer")

# Main input
col1, col2 = st.columns([4, 1])
with col1:
    url_input = st.text_input(
        "Product URL or Code",
        placeholder="https://www.saq.com/fr/10510354 or 10510354"
    )
with col2:
    st.write("")
    st.write("")
    search_main = st.button("🔍 Search", type="primary", use_container_width=True)

# Sample products
st.markdown("### Quick Test Products")
samples = [
    ("10510354", "Cazal Viel Vieilles Vignes", "14,30$"),
    ("11959348", "Cono Sur Bicicleta", "12,40$"),
    ("14678568", "M. Chapoutier Marius", "12,60$"),
]

cols = st.columns(3)
sample_clicked = None
for i, (code, name, price) in enumerate(samples):
    with cols[i]:
        if st.button(f"🍷 {name} ({price})", key=f"btn_{code}", use_container_width=True):
            sample_clicked = code

# Handle search trigger
search_url = None
if search_main and url_input:
    search_url = url_input.strip()
elif sample_clicked:
    search_url = sample_clicked

if search_url:
    with st.spinner(f"Scraping inventory... please wait (10-30 seconds)"):
        try:
            do_search(search_url, load_all)
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")
            st.info("Make sure the URL/code is valid")

# Display results
if st.session_state.results:
    results = st.session_state.results
    product = results["product"]
    inventory = results["inventory"]

    if location_filter:
        inventory = filter_inventory(inventory, location_filter)

    st.markdown("---")

    # Product card
    st.markdown(f"""
    <div class="product-card">
        <div class="product-name">{product.name}</div>
        <div>Code: {product.code}</div>
        <div class="product-price">{product.price}</div>
    </div>
    """, unsafe_allow_html=True)

    # Metrics
    total = sum(inv.quantity for inv in inventory)
    online = next((inv.quantity for inv in inventory if inv.store_id == "0"), 0)
    stores_with_stock = sum(1 for inv in inventory if inv.quantity > 0 and inv.store_id != "0")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total", f"{total:,}")
    m2.metric("Online", f"{online:,}")
    m3.metric("In Stores", f"{total - online:,}")
    m4.metric("Stores w/ Stock", stores_with_stock)

    # Table
    st.markdown("### Inventory by Store")

    df = pd.DataFrame([
        {
            "Status": get_stock_icon(inv.quantity),
            "Store": inv.store_name,
            "ID": inv.store_id,
            "Qty": inv.quantity,
        }
        for inv in inventory
    ])

    sort_by = st.selectbox("Sort", ["Qty (High-Low)", "Qty (Low-High)", "Store (A-Z)"])

    if sort_by == "Qty (High-Low)":
        df = df.sort_values("Qty", ascending=False)
    elif sort_by == "Qty (Low-High)":
        df = df.sort_values("Qty", ascending=True)
    else:
        df = df.sort_values("Store")

    st.dataframe(df, use_container_width=True, hide_index=True, height=400)

    # Chart
    st.markdown("### Top 15 Stores")
    top = df[df["ID"] != "0"].nlargest(15, "Qty")
    if not top.empty:
        st.bar_chart(top.set_index("Store")["Qty"])

    # Export
    c1, c2 = st.columns(2)
    with c1:
        st.download_button("📥 CSV", df.to_csv(index=False), f"saq_{product.code}.csv", "text/csv")
    with c2:
        st.download_button("📥 JSON", df.to_json(orient="records"), f"saq_{product.code}.json", "application/json")

    st.caption(f"Scraped: {results['scraped_at'].strftime('%Y-%m-%d %H:%M')}")

st.markdown("---")
st.markdown("Data from [SAQ.com](https://www.saq.com) | Built with Streamlit + Playwright")
