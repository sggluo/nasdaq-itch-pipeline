import pandas as pd 
import numpy as np
from src.config import DATA_DIR, BASE_DIR
from src.orderbook import get_dist, order_agg
import matplotlib.pyplot as plt
import seaborn as sns
import os
import polars as pl
from matplotlib.ticker import FuncFormatter
import seaborn as sns
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
from collections import defaultdict
import datetime
import matplotlib as mpl

def share_of_value_plot():
    stocks = pl.read_delta(os.path.join(DATA_DIR, 'mtype_R')).select(pl.col('stock_locate'), pl.col('stock'))
    P = pl.read_delta(os.path.join(DATA_DIR, 'mtype_P')).select(['stock_locate', 'shares', 'price'])
    Q = pl.read_delta(os.path.join(DATA_DIR, 'mtype_Q')).select(['stock_locate', 'shares', 'cross_price']).rename({'cross_price': 'price'})
    trades = pl.concat([P,Q], how='vertical').join(stocks, on='stock_locate')

    trades = trades.with_columns(   (pl.col('shares') * pl.col('price')).alias('value') )
    total_value = trades.select('value').sum().item()

    trades = trades.with_columns(   (pl.col('value') / total_value).alias('value_share') )

    trade_summary = trades.group_by('stock').agg( pl.col('value_share').sum()).sort('value_share',descending=True)

    trade_summary_pd = trade_summary.to_pandas()
    trade_summary_pd.iloc[:50].plot.bar(
         x='stock',
         y='value_share',
    figsize=(14, 6), color='darkblue', title='Share of Traded Value')

    plt.gca().yaxis.set_major_formatter(FuncFormatter(lambda y, _: '{:.0%}'.format(y)))
    sns.despine()
    plt.tight_layout()
    plt.show()

def dist_plot(pmin=240,pmax=250,bins=100,stock='AAPL'):
    fig, ax = plt.subplots(figsize=(7,5))
    hist_kws = {'linewidth': 1, 'alpha': .5}
    buys, sells = get_dist(pmin, pmax, bins=100,stock=stock)
    price_buy = buys['price'].to_numpy()
    price_sell = sells['price'].to_numpy()
    shares_buy = buys['total_shares'].to_numpy()
    shares_sell = sells['total_shares'].to_numpy()
    ax.bar(price_sell, shares_sell,
       width=0.1, alpha=0.5, label='Sell Shares')

    ax.bar(price_buy,  shares_buy,
       width=0.1, alpha=0.5, label='Buy Shares')

    ax.legend(fontsize=10)
    ax.set_title('Limit Order Price Distribution')


    ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _: f'{int(y/1000):,}'))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda x, _: f'${int(x):,}'))
    ax.set_xlabel('Price')
    ax.set_ylabel('Shares (\'000)')
    sns.despine()
    fig.tight_layout()
    plt.show()
  

def order_shape_ts_plot(stock='AAPL',date='10302019',depth=100):
    buy = pl.scan_delta( BASE_DIR/f'order_book/{stock}/buy').with_columns( (pl.col('price')*(1e-4)).alias('price'), pl.col('timestamp').cast(pl.Datetime('ns')).alias('timestamp')  ).sort('timestamp')
    sell = pl.scan_delta( BASE_DIR/f'order_book/{stock}/sell').with_columns( (pl.col('price')*(1e-4)).alias('price'), pl.col('timestamp').cast(pl.Datetime('ns')).alias('timestamp') ).sort('timestamp')
    buy = buy.filter(pl.col('price')> pl.col('price').quantile(0.01, 'nearest'))
    sell = sell.filter(pl.col('price')< pl.col('price').quantile(0.99, 'nearest'))

    buy_per_min, sell_per_min, trades_per_min =  order_agg(buy, sell)
    
    buy_price = buy_per_min['price'].to_numpy()
    buy_shares = buy_per_min['shares'].to_numpy()
    sell_price = sell_per_min['price'].to_numpy()
    sell_shares = sell_per_min['shares'].to_numpy()
    trades_price = trades_per_min['price'].to_numpy()

    buy_ts    = (buy_per_min['timestamp'].to_numpy()).astype('datetime64[ns]')
    sell_ts   = (sell_per_min['timestamp'].to_numpy()).astype('datetime64[ns]')
    trades_ts = (trades_per_min['timestamp'].to_numpy()).astype('datetime64[ns]')

    sns.set_style('white')
    fig, ax = plt.subplots(figsize=(14, 6))


    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    sc_buy  = ax.scatter(x=buy_ts,  y=buy_price,  c=buy_shares,  cmap='Reds',   alpha=.25)
    sc_sell = ax.scatter(x=sell_ts, y=sell_price, c=sell_shares, cmap='Greens', alpha=.25)
    
    sc_buy  = ax.scatter(x=buy_ts,  y=buy_price,  c=buy_shares,  cmap='Reds',   alpha=.25)
    sc_sell = ax.scatter(x=sell_ts, y=sell_price, c=sell_shares, cmap='Greens', alpha=.25)

    cbar_buy = fig.colorbar(sc_buy, ax=ax, location='left', pad=0.02)
    cbar_buy.set_label('Buy Shares', fontsize=10)


    cbar_sell = fig.colorbar(sc_sell, ax=ax, location='right', pad=0.02)
    cbar_sell.set_label('Sell Shares', fontsize=10)
    title = f'{stock} | {date} | Buy & Sell Limit Order Book | Depth = {depth}'
    fig.set_size_inches(14, 8)
    ax.plot(trades_ts, trades_price, c='k', lw=2, label='_nolegend_')
    ax.set_title(title)


    ax.set_xlabel('')
    ax.set_ylabel('Price', fontsize=12)


    green_patch = mpatches.Patch(color='green', label='Sell')
    red_patch = mpatches.Patch(color='red', label='Buy')

    ax.legend(handles=[red_patch, green_patch])
    sns.despine()
    fig.tight_layout()
    plt.show()

def raw_tick_plot(stock='AAPL', date='10302019'):
    trades_path = BASE_DIR/f'trades/{stock}'
    tick_bars = pl.scan_delta(trades_path).with_columns( (pl.col('price')*1e-4).alias('price'),
          pl.col('timestamp').cast(pl.Datetime('ns')).alias('timestamp') )\
           .filter( (pl.col('cross')==0) & (pl.col('timestamp').dt.time().is_between( datetime.time(9,30,0), datetime.time(16,0,0) ) ) )\
           .drop('cross').sort('timestamp').collect()  
   
    stock_bars = defaultdict()
    stock_bars[stock] = {'ts': tick_bars['timestamp'].to_numpy().astype('datetime64[ns]'),
     'price': tick_bars['price'].to_numpy() }

    sns.set_style('whitegrid')
    plt.plot(stock_bars[stock]['ts'],stock_bars[stock]['price'] )
    plt.xlabel('')
    plt.title(f'Tick Chart | {stock} | {date}')
    plt.tight_layout()
    plt.show()

def price_volume_plot(df, suptitle, price='vwap', bar='vol', fname=None):
    fig, axes = plt.subplots(nrows=2, sharex=True, figsize=(15,8))
    x = df['timestamp'].to_numpy().astype('datetime64[ns]')
    y = df[price].to_numpy()
    if bar=='c_vol':
        z = df['c_vol'].to_numpy()
    elif bar=='vol':
        z = df['volume'].to_numpy()
    elif bar=='d_val':
        z = df['c_value'].to_numpy()
    axes[0].plot(x,y)
    axes[1].bar(x,z, width=1/(5*z.shape[0]), color='r')

    #formatting
    xfmt = mpl.dates.DateFormatter('%H:%M')
    xlct = mpl.dates.HourLocator(interval=3)
    axes[0].xaxis.set_major_locator( xlct )
    axes[0].xaxis.set_major_formatter( xfmt )
    axes[0].get_xaxis().set_tick_params(which='major',pad=25)
    axes[0].set_title('Price', fontsize=14)
    axes[1].set_title('Volume', fontsize=14)

    fig.autofmt_xdate()
    fig.suptitle(suptitle)
    fig.tight_layout()
    plt.subplots_adjust(top=0.9)
