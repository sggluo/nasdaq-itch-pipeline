from pathlib import Path
import os
from collections import Counter
from datetime import timedelta

from time import time
import datetime

import pandas as pd
import polars as pl
import numpy as np
import deltalake
from deltalake import write_deltalake
from collections import defaultdict
from src.config import BASE_DIR, DATA_DIR, MESSAGE_DIR, TRADES_DIR

def format_time2(t):
    """Return a formatted time string 'HH:MM:SS
    based on a numeric time() value"""
    m, s = divmod(t, 60)
    h, m = divmod(m, 60)
    return f'{h:0>2.0f}:{m:0>2.0f}:{s:0>2.0f}'

def get_messages(date='10302019', stock='AAPL'):
    """Collect trading messages for given stock"""

    # Locate AAPL based on code AAPL
    stock_locate = pl.scan_delta(os.path.join(DATA_DIR, 'mtype_R')).filter(pl.col('stock') == stock).select(pl.col('stock_locate').cast(pl.Int64)).collect().limit(1).item()


    data = {}
    # trading message types
    # E: execured C: executed with price changed  X: order cancellation  D: order deletion
    # P: order message outside order book
    messages = ['A', 'F', 'E', 'C', 'X', 'D', 'U', 'P', 'Q']
    # collect messages related to the stock
    for m in messages:
      data[m] = pl.scan_delta(os.path.join(DATA_DIR, 'mtype_'+m)).with_columns(pl.col('stock_locate').cast(pl.Int64)).filter(pl.col('stock_locate') == stock_locate).drop('stock_locate')
      data[m] = data[m].with_columns(pl.lit(str(m) ).alias('type'))


    
    order_cols = ['order_reference_number', 'buy_sell_indicator', 'shares', 'price']
    # A: Add odder without MPID   F: Add order with MPID
    # MPID: market participant id
    orders = pl.concat([data['A'], data['F']],  how='diagonal').select(pl.col(order_cols))

    
    # E: execured C: executed with price changed  X: order cancellation  D: order deletion
    for m in messages[2: -3]:
        data[m] = data[m].join(orders,on='order_reference_number', how='left')

    
    # U: order replacement
    # new_order_ref|original_order_ref|price|shares|price_replaced|shares_replaced
    data['U'] = data['U'].join(orders, how='left',
                                right_on='order_reference_number',
                                left_on='original_order_reference_number',
                                suffix='_replaced')

    data['Q'] = data['Q'].rename({'cross_price': 'price'})

    # X: order cancellation
    data['X'] = data['X'].with_columns(
         pl.col('cancelled_shares').alias('shares')
)
    data['X'] = data['X'].drop_nulls(subset=['price'])


    data_ = pl.concat([data[m].collect() for m in messages] , how='diagonal').lazy()


    data = data_.with_columns(
      pl.lit(date).str.strptime(pl.Date, format='%m%d%Y').alias('date') )
    data = data.with_columns(
        (pl.col('date').cast(pl.Datetime('ns')) + pl.col('timestamp').cast(pl.Duration('ns'))).cast(pl.Int64).alias('timestamp') ).filter( pl.col('printable').cast(pl.Int64, strict=False).fill_null(1) != 0)

    drop_cols = ['tracking_number',  'attribution', 'match_number','cross_type',
                 'printable', 'date', 'cancelled_shares']
    existing_drop_cols = [c for c in drop_cols if c in data.columns]
    data = data.drop(existing_drop_cols).sort('timestamp',descending=False).collect()
    stock_message_path=MESSAGE_DIR/str(stock)
    os.makedirs(stock_message_path, exist_ok=True)
    write_deltalake(stock_message_path, data, mode='overwrite',storage_options={'allow_unsafe_rename': 'true'})
    return data
  
def get_trades(m,stock='AAPL'):
    """Combine C, E, P and Q messages into trading records"""
    trade_dict = {'executed_shares': 'shares', 'execution_price': 'price'}
    cols = ['timestamp', 'executed_shares']
    data =  pl.concat(
        [ # E: executed
          m.filter( (pl.col('type') == 'E') ).select( cols+['price']  ).rename( {'executed_shares': 'shares'}),
           # C: executed with price changed
           m.filter( (pl.col('type') == 'C') ).select( cols+['execution_price']  ).rename( trade_dict),
            # P: order message outside order book
            m.filter( (pl.col('type') == 'P') ).select( ['timestamp', 'price', 'shares'] ),
            # Q: cross trade messages
             m.filter( (pl.col('type') == 'Q') ).select( ['timestamp', 'price', 'shares', pl.lit(1).alias('cross')])
        ], how='diagonal'
    ).drop_nulls(subset=['price']).fill_null(0).sort('timestamp',descending=False).cast({col: pl.Int64 for col in ['shares', 'price', 'cross']}, strict=False)
    trader_stock_path = TRADES_DIR/str(stock)
    os.makedirs(trader_stock_path, exist_ok=True)
    write_deltalake(trader_stock_path, data, mode='overwrite', storage_options={'allow_unsafe_rename':'true'})
    return data

def save_orders(orders, append=False,stock='AAPL'):
    cols = ['price', 'shares']

    # buysell: indicator 1,-1 
    # book: {timestamp: [(price1, volume1),(price2, volume2)...] }
    for buysell, book in orders.items():
        if not book:  
            continue
        df = pl.concat([pl.DataFrame(data=data,schema=cols, orient='row')
                   .with_columns( pl.lit(t).cast(pl.Int64).alias('timestamp') )
                 for t, data in book.items()] ).with_columns( pl.col(cols).cast(pl.Int64) ).sort('timestamp')

        order_book_stock_buy_path = BASE_DIR/f'order_book/{stock}/buy'
        order_book_stock_sell_path = BASE_DIR/f'order_book/{stock}/sell'
        os.makedirs(order_book_stock_buy_path, exist_ok=True)
        os.makedirs(order_book_stock_sell_path, exist_ok=True)

        if buysell==1:
            if append:
                write_deltalake(order_book_stock_buy_path , df, mode='append',storage_options={'allow_unsafe_rename':'true'})
            else:
                write_deltalake(order_book_stock_buy_path , df, mode='overwrite',storage_options={'allow_unsafe_rename':'true'})
        else:
            if append:
                write_deltalake(order_book_stock_sell_path , df, mode='append',storage_options={'allow_unsafe_rename':'true'})
            else:
                write_deltalake(order_book_stock_sell_path , df, mode='overwrite',storage_options={'allow_unsafe_rename':'true'})
            


def sort_price(side, price_agg, nlevels):
    if side ==1:
        return sorted([(p,q) for p,q in price_agg.items() if q > 0], key= lambda x: x[0], reverse=True )[:nlevels]
    else:
        return sorted([(p,q) for p,q in price_agg.items() if q > 0], key= lambda x: x[0] )[:nlevels]



def order_book_builder(messages, nlevels=100,stock='AAPL'):
    # -1: sell, 1: buy
    # price_agg[-1] = {price: volume }
    price_agg = {-1: defaultdict(int), 1: defaultdict(int)}
    # active order ={order ref： {price: ,shares: ,side: }}
    active_orders= {}
    # order_book ={-1:{timestamp: (price1, volume1),(price2,volume2)...}, 1:{timestamp:(price1, volume1),(price2,volume2)...} }
    order_book = {-1: {}, 1: {}}
    message_counter = defaultdict(int)
   

    # executed shares
    messages = messages.with_columns( pl.when(pl.col('type').is_in(['E', 'C']))
      .then(pl.col('executed_shares'))
      .otherwise(pl.col('shares'))
      .alias('shares') )

    # transform columns of dataframe to lists
    timestamps = messages['timestamp'].to_list()
    types = messages['type'].to_list()
    prices = messages['price'].to_list()
    shares_list = messages['shares'].to_list()
    buy_sells = messages['buy_sell_indicator'].to_list()
    prices_replaced  = messages['price_replaced'].to_list()
    shares_replaced  = messages['shares_replaced'].to_list()
    order_ref = messages['order_reference_number'].to_list()
    orgn_order_ref = messages['original_order_reference_number'].to_list()
    new_order_ref = messages['new_order_reference_number'].to_list()

    start = time()
    # for each timestamp 
    for i in range(len(timestamps)):

        if (i + 1) % int(1e5) == 0 and i > 0:
            print(f'{i+1:,.0f}\t\t{format_time2(time() - start)}')
            save_orders(order_book, append=True, stock=stock)
            # clear order_book
            order_book = {-1: {}, 1: {}}
            start = time()

        side = buy_sells[i]
        if not side is None:
            side = int(side)
        
        msg_type  = types[i]
        timestamp = timestamps[i]

        price, shares = None, None
        # count message types
        message_counter[msg_type] += 1

        if msg_type in ['A', 'F']: # add orders
            if side not in (-1, 1) or prices[i] is None or shares_list[i] is None:
                continue
            price  = int(prices[i])
            shares = int(shares_list[i])
            ref = int(order_ref[i])
            active_orders[ref] = {'price': price, 'shares': shares, 'side': side}
            price_agg[side][price] += shares
            # update orderbook
            order_book[side][timestamp] =  sort_price(side, price_agg[side], nlevels)

        elif msg_type == 'X': # cancel order (partially)
            ref = int(order_ref[i])
            if shares_list[i] is None:
                continue
            cancelled = int(shares_list[i])
            if ref in active_orders: # identify if still in order book
                price  = int( active_orders[ref]['price'] )
                side = int(active_orders[ref]['side'] )
                # shares decreased
                active_orders[ref]['shares'] -= cancelled
                if active_orders[ref]['shares'] <= 0:
                    del active_orders[ref]

                price_agg[side][price] -= cancelled
                if price_agg[side][price] <=0:
                    del price_agg[side][price]
                order_book[side][timestamp] =  sort_price(side, price_agg[side], nlevels)

        elif msg_type in ['E', 'C']:
            ref = int(order_ref[i])
            executed = int(shares_list[i])

            if ref in active_orders:
                display_price = int( active_orders[ref]['price'] )
                side = int( active_orders[ref]['side'] )
                active_orders[ref]['shares'] -= executed
                if active_orders[ref]['shares'] <= 0:
                    del active_orders[ref]

                price_agg[side][display_price] -= executed
                if price_agg[side][display_price] <=0:
                    del price_agg[side][display_price]
                order_book[side][timestamp] =  sort_price(side, price_agg[side], nlevels)

        elif msg_type == 'D':
            ref = int(order_ref[i])

            if ref in active_orders:
                price = int( active_orders[ref]['price'] )
                del_shares = int( active_orders[ref]['shares'] )
                side = active_orders[ref]['side']
                del active_orders[ref]

                price_agg[side][price] -= del_shares
                if price_agg[side][price]<=0:
                    del price_agg[side][price]
                order_book[side][timestamp] =  sort_price(side, price_agg[side], nlevels)

        elif msg_type == 'U':
            orig_ref = int(orgn_order_ref[i])
            new_ref = int(new_order_ref[i])

            if orig_ref in active_orders:
                side =  int( active_orders[orig_ref]['side'] )
                orig_price = int( active_orders[orig_ref]['price'] )
                orig_shares = int( active_orders[orig_ref]['shares'] )
                del active_orders[orig_ref]
                price_agg[side][orig_price] -= orig_shares
                if price_agg[side][orig_price] <=0:
                    del price_agg[side][orig_price]

                price = int(prices[i])
                shares = int(shares_list[i])
                active_orders[new_ref] = {'price': price, 'shares': shares, 'side': side}
                price_agg[side][price] += shares
                order_book[side][timestamp] =  sort_price(side, price_agg[side], nlevels)
            else:
                side_new = buy_sells[i]
                if side_new in (-1, 1) and prices[i] is not None and shares_list[i] is not None:
                    p = int(prices[i])
                    s = int(shares_list[i])
                    active_orders[new_ref] = {'price': p, 'shares': s, 'side': int(side_new)}
                    price_agg[int(side_new)][p] += s
                    order_book[int(side_new)][timestamp] = sort_price(int(side_new), price_agg[int(side_new)], nlevels)



    print('Saving remaining orders...')
    save_orders(order_book, append=True, stock=stock)
    print('Done.')
    
def get_dist(pmin=240, pmax=250, bins=50,stock='AAPL'):
    buy = pl.scan_delta( BASE_DIR/f'order_book/{stock}/buy').with_columns( (pl.col('price')*(1e-4)).alias('price'), pl.col('timestamp').cast(pl.Datetime('ns')).alias('timestamp')  ).sort('timestamp')
    sell = pl.scan_delta( BASE_DIR/f'order_book/{stock}/sell').with_columns( (pl.col('price')*(1e-4)).alias('price'), pl.col('timestamp').cast(pl.Datetime('ns')).alias('timestamp') ).sort('timestamp')
    buy = buy.filter(pl.col('price')> pl.col('price').quantile(0.01, 'nearest'))
    sell = sell.filter(pl.col('price')< pl.col('price').quantile(0.99, 'nearest'))
    buy = buy.filter(  (pl.col('price') > pmin) &
           (pl.col('price') < pmax) & (pl.col('timestamp').dt.time().is_between( datetime.time(9,30,0), datetime.time(16,0,0) )) )\
           .with_columns(
               ( (pl.col('price')-pmin)/(pmax-pmin)*bins).floor().cast(pl.Int64).alias('bins')   ).group_by('bins')\
                 .agg(pl.col('shares').sum().alias('total_shares'))\
                 .sort('bins').with_columns(
                     (pmin + (pl.col('bins') + 0.5)*(pmax-pmin)/bins).alias('price')    )
    sell = sell.filter(  (pl.col('price') > pmin) &
           (pl.col('price') < pmax) & (pl.col('timestamp').dt.time().is_between( datetime.time(9,30,0), datetime.time(16,0,0) )) )\
           .with_columns(
               ( (pl.col('price')-pmin)/(pmax-pmin)*bins).floor().cast(pl.Int64).alias('bins')   ).group_by('bins')\
                 .agg(pl.col('shares').sum().alias('total_shares'))\
                 .sort('bins').with_columns(
                     (pmin + (pl.col('bins') + 0.5)*(pmax-pmin)/bins).alias('price')    )
    return buy.collect(), sell.collect()

def order_agg(buy, sell, depth=100, stock='AAPL'):
    utc_offset = timedelta(hours=4)

    if  isinstance(buy, pl.DataFrame ):
         buy = buy.lazy()
    if  isinstance(sell, pl.DataFrame ):
         sell = sell.lazy()
    
    if buy.collect_schema()['timestamp'] == pl.Int64:
        buy = buy.with_columns(pl.col('timestamp').cast(pl.Datetime('ns')))
    if sell.collect_schema()['timestamp'] == pl.Int64:
        sell = sell.with_columns(pl.col('timestamp').cast(pl.Datetime('ns')))

    buy_per_min = buy.with_columns(
      pl.col('timestamp').dt.truncate('1m') )\
       .group_by( ['timestamp', 'price'] ).agg( pl.col('shares').sum().log1p().alias('shares') )\
       .filter(pl.col('timestamp').dt.time().is_between(datetime.time(9,30,0),datetime.time(16,0,0) ) )\
       .sort( ['timestamp', 'price'] ,descending=[False, True])\
       .group_by('timestamp').agg(pl.col('price').head(depth), pl.col('shares').head(depth)) \
       .explode(['price', 'shares'])\
       .with_columns(  pl.col('timestamp').cast(pl.Int64)   ).collect()


    sell_per_min = sell.with_columns(
        pl.col('timestamp').dt.truncate('1m') )\
        .group_by( ['timestamp', 'price'] ).agg( pl.col('shares').sum().log1p().alias('shares') )\
        .filter(pl.col('timestamp').dt.time().is_between(datetime.time(9,30,0),datetime.time(16,0,0) ) )\
        .sort( ['timestamp', 'price'] ,descending=[False, False])\
        .group_by('timestamp').agg(pl.col('price').head(depth), pl.col('shares').head(depth)) \
        .explode(['price', 'shares'])\
        .with_columns(  pl.col('timestamp').cast(pl.Int64) ).collect()



    trades_per_min = pl.scan_delta( BASE_DIR/f'trades/{stock}'   ).with_columns(
    (pl.col('price')*1e-4).alias('price'),
       pl.col('timestamp').cast(pl.Datetime('ns')).alias('timestamp')).filter( (pl.col('cross') == 0)  &
         pl.col('timestamp').dt.time().is_between(datetime.time(9,30,0),datetime.time(16,0,0) )
          )\
            .with_columns( pl.col('timestamp').dt.truncate('1m') ).group_by('timestamp')\
            .agg(pl.col('price').mean().alias('price'), pl.col('shares').sum().alias('shares') )\
            .with_columns( pl.col('timestamp').cast(pl.Int64) ).sort('timestamp').collect()
    
    return buy_per_min,sell_per_min,trades_per_min
