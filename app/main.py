import os
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import altair as alt
from sqlalchemy import create_engine

# -------------------------------------------------------------------
# 1. Connexion à la base Postgres
# -------------------------------------------------------------------

# Par défaut, on considère que tu es en docker-compose, avec un service "db"
# Tu peux surcharger avec une variable d'environnement DATABASE_URL si besoin.
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg2://app_user:app_password@db:5432/mba_db",
)

engine = create_engine(DATABASE_URL)


# -------------------------------------------------------------------
# 2. Chargement des données depuis Postgres
# -------------------------------------------------------------------
@st.cache_data
def load_data() -> pd.DataFrame:
    """
    Charge les données depuis la table 'transactions' de Postgres
    et prépare les variables nécessaires pour le dashboard.
    """
    query = """
        SELECT
            bill_no     AS "BillNo",
            itemname    AS "Itemname",
            quantity    AS "Quantity",
            date        AS "Date",
            price       AS "Price",
            customer_id AS "CustomerID",
            country     AS "Country"
        FROM transactions;
    """

    df = pd.read_sql(query, engine)

    # Types
    df["Date"] = pd.to_datetime(df["Date"])
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype(int)
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce")

    # Features dérivées
    df["Revenue"] = df["Quantity"] * df["Price"]
    df["InvoiceDate"] = df["Date"].dt.date               # pur type date → pour le filtre
    df["InvoiceMonth"] = df["Date"].dt.to_period("M").dt.to_timestamp()  # pour les graphes mensuels
    df["Hour"] = df["Date"].dt.hour
    df["Weekday"] = df["Date"].dt.day_name()

    return df


# -------------------------------------------------------------------
# 3. Filtres (période, pays, quantité)
# -------------------------------------------------------------------
def filter_data(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("Filtres")

    if df.empty:
        st.sidebar.warning("Aucune donnée disponible.")
        return df

    # S'assurer que InvoiceDate est bien un type date
    if not pd.api.types.is_datetime64_any_dtype(df["InvoiceDate"]):
        df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"]).dt.date

    min_date = df["InvoiceDate"].min()
    max_date = df["InvoiceDate"].max()

    if min_date is None or pd.isna(min_date):
        min_date = date.today()
    if max_date is None or pd.isna(max_date):
        max_date = date.today()

    # --------- Filtre période ---------
    date_range = st.sidebar.date_input(
        "Période",
        value=(min_date, max_date),
        key="periode_filtre",
    )

    if isinstance(date_range, tuple):
        if len(date_range) == 2:
            start_date, end_date = date_range
        else:
            start_date = end_date = date_range[0]
    else:
        start_date = end_date = date_range

    filtered = df[
        (df["InvoiceDate"] >= start_date)
        & (df["InvoiceDate"] <= end_date)
    ]

    # --------- Filtre pays ---------
    countries = sorted(filtered["Country"].dropna().unique().tolist())
    selected_countries = st.sidebar.multiselect(
        "Pays", options=countries, default=countries, key="filtre_pays"
    )

    filtered = filtered[filtered["Country"].isin(selected_countries)]

    # Si plus aucune ligne après période + pays → on s'arrête là
    if filtered.empty:
        st.sidebar.warning("Aucune ligne après filtres période et pays.")
        return filtered

    # --------- Filtre quantité ---------
    min_quantity = int(filtered["Quantity"].min())
    max_quantity = int(filtered["Quantity"].max())

    if min_quantity == max_quantity:
        # Pas de slider possible, on fixe juste l'info
        st.sidebar.info(f"Toutes les lignes ont Quantity = {min_quantity}.")
        quantity_threshold = min_quantity
    else:
        quantity_threshold = st.sidebar.slider(
            "Seuil minimum de quantité par ligne",
            min_value=min_quantity,
            max_value=max_quantity,
            value=min_quantity,
            key="filtre_quantite",
        )

    filtered = filtered[filtered["Quantity"] >= quantity_threshold]

    return filtered


# -------------------------------------------------------------------
# 4. Sections de dashboard
# -------------------------------------------------------------------
def kpi_section(df: pd.DataFrame):
    col1, col2, col3, col4 = st.columns(4)

    n_transactions = df["BillNo"].nunique()
    n_items = df["Itemname"].nunique()
    n_customers = df["CustomerID"].nunique()
    total_revenue = df["Revenue"].sum()

    col1.metric("Transactions", f"{n_transactions:,}".replace(",", " "))
    col2.metric("Articles distincts", f"{n_items:,}".replace(",", " "))

    if not np.isnan(n_customers):
        col3.metric("Clients", f"{int(n_customers):,}".replace(",", " "))
    else:
        col3.metric("Clients", "N/A")

    col4.metric(
        "Chiffre d'affaires",
        f"{total_revenue:,.0f} €".replace(",", " ").replace(".", " "),
    )

    st.caption(
        "Le chiffre d'affaires est calculé comme `Quantity × Price` sur l'échantillon filtré."
    )


def transactions_over_time(df: pd.DataFrame):
    st.subheader("Transactions dans le temps")

    freq = st.radio(
        "Granularité",
        options=["Jour", "Mois"],
        horizontal=True,
        key="freq_radio",
    )

    if freq == "Jour":
        ts = (
            df.groupby("InvoiceDate")["BillNo"]
            .nunique()
            .reset_index(name="Transactions")
        )
        chart = alt.Chart(ts).mark_line(point=True).encode(
            x=alt.X("InvoiceDate:T", title="Date"),
            y=alt.Y("Transactions:Q", title="Nombre de transactions"),
            tooltip=["InvoiceDate:T", "Transactions:Q"],
        )
    else:
        ts = (
            df.groupby("InvoiceMonth")["BillNo"]
            .nunique()
            .reset_index(name="Transactions")
        )
        chart = alt.Chart(ts).mark_line(point=True).encode(
            x=alt.X("InvoiceMonth:T", title="Mois"),
            y=alt.Y("Transactions:Q", title="Nombre de transactions"),
            tooltip=["InvoiceMonth:T", "Transactions:Q"],
        )

    st.altair_chart(chart.properties(height=300), use_container_width=True)


def top_products(df: pd.DataFrame):
    st.subheader("Top produits")

    if df.empty:
        st.info("Aucune donnée disponible pour les produits avec les filtres actuels.")
        return

    mode = st.radio(
        "Classer par :",
        options=["Quantité vendue", "Chiffre d'affaires"],
        horizontal=True,
        key="top_mode",
    )

    agg = (
        df.groupby("Itemname")
        .agg(
            quantity_sold=("Quantity", "sum"),
            revenue=("Revenue", "sum"),
            n_transactions=("BillNo", "nunique"),
        )
        .reset_index()
    )

    if mode == "Quantité vendue":
        agg = agg.sort_values("quantity_sold", ascending=False)
        value_col = "quantity_sold"
        title = "Top produits par quantité"
    else:
        agg = agg.sort_values("revenue", ascending=False)
        value_col = "revenue"
        title = "Top produits par chiffre d'affaires"

    n_products = len(agg)
    if n_products == 0:
        st.info("Aucun produit après filtrage.")
        return

    if n_products == 1:
        st.sidebar.info("Un seul produit disponible avec les filtres actuels.")
        top_n = 1
    else:
        top_n = st.sidebar.slider(
            "Nombre de produits à afficher",
            min_value=1,
            max_value=n_products,
            value=min(10, n_products),
            key="top_n_slider",
        )

    top = agg.head(top_n)

    chart = (
        alt.Chart(top)
        .mark_bar()
        .encode(
            x=alt.X(f"{value_col}:Q", title=mode),
            y=alt.Y("Itemname:N", sort="-x", title="Produit"),
            tooltip=["Itemname:N", "quantity_sold:Q", "revenue:Q", "n_transactions:Q"],
        )
    )

    st.altair_chart(chart.properties(title=title, height=400), use_container_width=True)

    with st.expander("Table des produits (top)"):
        st.dataframe(top)



def basket_analysis(df: pd.DataFrame):
    st.subheader("Analyse des paniers (Market Basket)")

    if df.empty:
        st.info("Aucune donnée disponible pour l'analyse des paniers avec les filtres actuels.")
        return

    # On agrège par ticket (BillNo)
    baskets = df.groupby("BillNo").agg(
        basket_size=("Itemname", "nunique"),
        quantity_total=("Quantity", "sum"),
        revenue=("Revenue", "sum"),
    )

    # KPIs
    col1, col2, col3 = st.columns(3)
    col1.metric("Taille moyenne du panier", f"{baskets['basket_size'].mean():.2f}")
    col2.metric(
        "Quantité moyenne par panier", f"{baskets['quantity_total'].mean():.2f}"
    )
    col3.metric("CA moyen par panier", f"{baskets['revenue'].mean():.2f} €")

    # Distribution de la taille des paniers
    size_counts = (
        baskets["basket_size"]
        .value_counts()
        .rename_axis("basket_size")   # le nom de l'index devient 'basket_size'
        .reset_index(name="count")    # la série de counts devient une colonne 'count'
        .sort_values("basket_size")
    )

    chart = (
        alt.Chart(size_counts)
        .mark_bar()
        .encode(
            x=alt.X(
                "basket_size:O",
                title="Taille de panier (nb d'articles distincts)",
            ),
            y=alt.Y("count:Q", title="Nombre de transactions"),
            tooltip=["basket_size:O", "count:Q"],
        )
    )

    st.altair_chart(
        chart.properties(
            title="Distribution de la taille des paniers",
            height=300,
        ),
        use_container_width=True,
    )

    with st.expander("Table de distribution des tailles de paniers"):
        st.dataframe(size_counts)



def country_analysis(df: pd.DataFrame):
    st.subheader("Répartition géographique")

    if df.empty:
        st.info("Aucune donnée disponible pour la répartition géographique.")
        return

    country_stats = (
        df.groupby("Country")
        .agg(
            transactions=("BillNo", "nunique"),
            revenue=("Revenue", "sum"),
        )
        .reset_index()
        .sort_values("revenue", ascending=False)
    )

    n_countries = len(country_stats)
    if n_countries == 0:
        st.info("Aucun pays trouvé après filtrage.")
        return

    # Si un seul pays → pas de slider, on affiche juste ce pays
    if n_countries == 1:
        st.sidebar.info("Un seul pays disponible avec les filtres actuels.")
        top_countries = 1
    else:
        # Au moins 2 pays → slider entre 1 et n_countries
        top_countries = st.sidebar.slider(
            "Nombre de pays à afficher",
            min_value=1,
            max_value=n_countries,
            value=min(10, n_countries),
            key="top_countries_slider",
        )

    top = country_stats.head(top_countries)

    chart = (
        alt.Chart(top)
        .mark_bar()
        .encode(
            x=alt.X("revenue:Q", title="Chiffre d'affaires"),
            y=alt.Y("Country:N", sort="-x", title="Pays"),
            tooltip=["Country:N", "transactions:Q", "revenue:Q"],
        )
    )

    st.altair_chart(
        chart.properties(title="CA par pays", height=300),
        use_container_width=True,
    )

    with st.expander("Table complète pays"):
        st.dataframe(country_stats)



def temporal_pattern(df: pd.DataFrame):
    st.subheader("Patterns temporels (jour de la semaine × heure)")

    heat = (
        df.groupby(["Weekday", "Hour"])["BillNo"]
        .nunique()
        .reset_index(name="transactions")
    )

    weekday_order = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    heat["Weekday"] = pd.Categorical(
        heat["Weekday"], categories=weekday_order, ordered=True
    )

    chart = (
        alt.Chart(heat)
        .mark_rect()
        .encode(
            x=alt.X("Hour:O", title="Heure"),
            y=alt.Y("Weekday:O", title="Jour de la semaine"),
            color=alt.Color("transactions:Q", title="Nb transactions"),
            tooltip=["Weekday:O", "Hour:O", "transactions:Q"],
        )
    )

    st.altair_chart(
        chart.properties(
            title="Chaleur des transactions par heure et par jour",
            height=300,
        ),
        use_container_width=True,
    )


# -------------------------------------------------------------------
# 5. App principale
# -------------------------------------------------------------------
def main():
    st.set_page_config(
        page_title="Market Basket Analysis – Dashboard",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    st.title("Market Basket Analysis – Dashboard")

    df = load_data()

    if df.empty:
        st.error("Aucune donnée chargée depuis la base de données.")
        return

    filtered_df = filter_data(df)

    st.info(f"{len(filtered_df):,} lignes après filtrage.".replace(",", " "))

    # KPIs globales
    kpi_section(filtered_df)

    # Onglets
    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        ["Vue temporelle", "Top produits", "Paniers", "Pays", "Patterns temporels"]
    )

    with tab1:
        transactions_over_time(filtered_df)
    with tab2:
        top_products(filtered_df)
    with tab3:
        basket_analysis(filtered_df)
    with tab4:
        country_analysis(filtered_df)
    with tab5:
        temporal_pattern(filtered_df)

    with st.expander("Aperçu des données brutes"):
        st.dataframe(filtered_df.head(100))


if __name__ == "__main__":
    main()
