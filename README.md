# Nasdaq ITCH Data Pipeline & Market Microstructure Analysis

A high-performance Python pipeline for processing **Nasdaq TotalView-ITCH** binary data. This project parses raw financial messages, reconstructs the Limit Order Book (LOB), aggregates trades into various bar types (Time, Volume, Dollar), and generates high-quality visualizations for market microstructure analysis.

## 🚀 Key Features

- **Binary ITCH Parser:** Efficiently parses raw ITCH binary files into structured tabular data.
- **Ultra-fast Processing:** Utilizes `Polars` for lightning-fast dataframe operations and lazy evaluations.
- **Delta Lake Storage:** Stores parsed messages, trades, and reconstructed order book states in Delta Lake format for ACID transactions and fast queries.
- **Limit Order Book Reconstruction:** Builds the order book dynamically from Add, Cancel, Execute, Delete, and Replace messages.
- **Advanced Bar Aggregation:** Generates Time Bars, Volume Bars, and Dollar Bars along with VWAP and OHLCV metrics.
- **Rich Visualizations:** 
  - Order book depth dynamics over time (Heatmaps with bid/ask accumulation).
  - Price vs. Volume/Dollar value dual-axis charts.
  - Raw tick charts.

## 📁 Project Structure

```text
├── data/                   # Directory for raw and processed data (Ignored by Git)
├── src/
│   ├── config.py           # Global configurations (Paths, Encondings, Formats)
│   ├── parser.py           # ITCH binary parsing logic
│   ├── orderbook.py        # Order book rebuilding and trade extraction
│   ├── visualizer.py       # Plotting functions for microstructure analysis
│   └── ...
|—— order_book              # prices & volumes of the order book rebuilt
|—— trades                  # trade data
|—— messages                # messages with ITCH data
├── .gitignore
├── requirements.txt        # Python dependencies
└── README.md
