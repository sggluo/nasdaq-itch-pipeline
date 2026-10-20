import polars as pl
from collections import namedtuple, Counter, defaultdict
import os
from deltalake import write_deltalake
from struct import unpack          
from time import time  
import pandas as pd
from src.config import ENCODINGS, DATA_DIR
import datetime

"""
one ITCH messages has the following structure:
0, 1 | 2 | 3, 4...
message length | message type | message body
"""

def message_types_parser(message_type_dir, formats):
    df = pl.read_excel(message_type_dir, sheet_name='messages').sort('id').drop('id')
    df = df.rename({ c:c.lower().strip() for c in df.columns })
    df = df.with_columns(
       pl.col('name').str.strip_chars()
              .str.to_lowercase()
              .str.replace_all(' ', '_')
              .str.replace_all('-', '_')
              .str.replace_all('/', '_'),
       pl.col('value').str.strip_chars(),
       pl.col('notes').str.strip_chars(),
   )
    # add a new column that has the values of variable types (e.g. Integral, S, Alpha)
    # when the value of name column is 'message type', otherwise 'None'
    # df:  *******|   name    | message_type
    #          message_type      s
    #          event_code      alpha
    df = df.with_columns( pl.when( pl.col('name')=='message_type' )
           .then( pl.col('value') ).alias('message_type')  )

    # a new dataframe with columns 'message_type' and 'name' (originally 'notes' )
    # 'None' values have been dropped 
    # message_labels: message_type | name(originally 'notes' )
    #            S          ******
    #            R         **********  
    message_labels = ( df.select( pl.col('message_type'), pl.col('notes') )
               .drop_nulls().rename({'notes': 'name'})  )
    message_labels = message_labels.with_columns( pl.col('name')
                       .str.to_lowercase()
                       .str.replace_all('message', '')
                       .str.replace_all('.', '',literal=True)
                       .str.strip_chars()
                       .str.replace_all(' ', '_')
                        )
    # since the excel file groups messages by message type
    message_types = (df.with_columns( pl.col('message_type').forward_fill())
              .filter(pl.col('name') != 'message_type') )
    message_types = message_types.with_columns(pl.col('value')
                       .str.to_lowercase()
                       .str.replace_all(' ', '_',literal=True)
                       .str.replace_all('(', '', literal=True)
                       .str.replace_all(')', '', literal=True)
                       )
   
    formats_df = pl.DataFrame(
     { 'length': [v2 for v1,v2 in formats.keys()],
      'value': [v1 for v1,v2 in formats.keys()],
      'formats': [f for _, f in formats.items()]
    }
)
    message_types = message_types.join(formats_df, on =['length','value'], how='left' )

    message_fields, fstring = {}, {}
    # msgtg: message_type |  name | formats
    #              [...]  [...]
    msgtg = message_types.group_by('message_type').agg( pl.col('name'), pl.col('formats') )
    
    # iterate by rows
    for row in msgtg.rows():

        # {type: a tuple with name ('message_type'), and field names ('names')}
        message_fields[row[0]] = namedtuple(typename=row[0], field_names=row[1])

        # {type: >.'formats'}    ('>' means big-endian)
        # 'D': '>HH6sQ', 'B': '>HH6sQ', 'Y': '>HH6s8ss' ...
        fstring[row[0]] = '>'+''.join(row[2])
    return message_types, message_labels, message_fields, fstring

def alpha_parser(message_types):
    alpha_fields = message_types.filter( pl.col('value')=='alpha' )
    # alpha_msgs: message_types | ......
    alpha_msgs = alpha_fields.group_by('message_type').agg(pl.col('*'))

    alpha_formats ={}
    alpha_length ={}

    for row in alpha_msgs.rows():
        dict2 = {}
        dict3 = {}
        # row[1] is 'name'
        # (0,name0), (1,name1)
        for i,name in enumerate(row[1]):
            # name i: format i
            dict2[name] = row[6][i]
            # name i: length i
            dict3[name] = row[3][i]
        # message type x: {names: formats}           
        alpha_formats[row[0]] = dict2
        # message type x: {names: lengths}
        alpha_length[row[0]] = dict3
    return alpha_formats, alpha_length

def format_alpha(mtype, data, alpha_formats):
    """Process byte strings of type alpha"""
    # col belong to names
    for col in alpha_formats.get(mtype).keys():
        if mtype != 'R' and col == 'stock':
            data = data.drop(col)
            continue
        data = data.with_columns(
            pl.col(col).cast(pl.Utf8)
                     .str.strip_chars()
        )
        if ENCODINGS.get(col):
            F_mapping = ENCODINGS.get(col)
            # conver Alpha values to categorical values, e.g. 0, 1, -1
            data = data.with_columns( pl.col(col).replace(F_mapping)
            )
    return data

def store_messages(m, alpha_formats):
    """
    Handle occasional storing of all messages
    m: dict, messages={message_type: tuples}
    """

    for mtype, data in m.items():
            # convert to DataFrame from named tuple
            data = pl.DataFrame(data)
            if data.is_empty():
                  continue

            # parse timestamp info
            ts_dtype = data.schema['timestamp']
            if ts_dtype == pl.Binary:
                timestamps = [
                int.from_bytes(b, 'big') for b in data['timestamp'].to_list()
    ]
                data = data.with_columns(
                     pl.Series('timestamp', timestamps, dtype=pl.Int64)
    )
            else:
                data = data.with_columns(
                    pl.col('timestamp').cast(pl.Int64).alias('timestamp')
                )


            # apply alpha formatting
            if mtype in alpha_formats.keys():
                data = format_alpha(mtype, data, alpha_formats)
            table_path = os.path.join(DATA_DIR, f'mtype_{mtype}')
            try:
                write_deltalake(table_path, data, mode='append',storage_options={"allow_unsafe_rename": "true"})
            except Exception as e:
                print(e)
                print(mtype)
                print(data.schema)
                print(pl.Series(list(m.keys())).value_counts())
                data.write_csv('data_pl.csv')
                return 1
    return 0

def format_time(t):
    """Return a formatted time string 'HH:MM:SS based on a numeric time() value"""
    m, s = divmod(t, 60)
    h, m = divmod(m, 60)
    # '01:01:01.50'
    return f'{h:0>2.0f}:{m:0>2.0f}:{s:0>5.2f}'

def binary_parser(filename, message_fields, fstring, alpha_formats, event_codes):
    messages = defaultdict(list)
    message_count = 0
    message_type_counter = Counter()

    start = time()
    with filename.open('rb') as data:
        while True:

            # determine message size from the first 2 bytes
            message_size = int.from_bytes(data.read(2), byteorder='big', signed=False)
            if not message_size:
                break
            # get message type by reading one more byte
            message_type = data.read(1).decode('ascii')
            # count message types
            message_type_counter.update([message_type])

            # read & store message
            try:  
                # read the message body, wihtout type
                record = data.read(message_size - 1) 
                # unpack: parse record according to fstring[...]
                # _make: fill the named tuple with values from unpacking
                # message: an instance of named tuple
                message = message_fields[message_type]._make(unpack(fstring[message_type], record))
                # messages is a dictionary: {[],[],[]}, one tuple joins the list
                messages[message_type].append(message)
            except Exception as e:
                print(e)
                print(message_type)
                print(record)
                print(fstring[message_type])
                continue

            # deal with system events
            if message_type == 'S':
                seconds = int.from_bytes(message.timestamp, byteorder='big') * 1e-9
                # print the system events
                print('\n', event_codes.get(message.event_code.decode('ascii'), 'Error'))
                # 1. timestamp; 2. how many events happened by the time
                print(f'\t{format_time(seconds)}\t{message_count:12,.0f}')
                #  'C': 'End of Messages'
                if message.event_code.decode('ascii') == 'C':
                    store_messages(messages, alpha_formats)
                    messages.clear() # messages become empty
                    break
            message_count += 1

            if message_count % 5e6 == 0:
                seconds = int.from_bytes(message.timestamp, byteorder='big') * 1e-9
                d = format_time(time() - start)
                print(f'\t{format_time(seconds)}\t{message_count:12,.0f}\t{d}')
                res = store_messages(messages, alpha_formats)
                if res == 1:
                    print(pd.Series(dict(message_type_counter)).sort_values())
                    break
                messages.clear() # messages become empty
    if messages:
        res = store_messages(messages,alpha_formats)
        if res == 1:
            print("Fail to store the remaining messages.")
            messages.clear()

    print('Duration:', format_time(time() - start))

    counter = pl.DataFrame({
        'message_type': list(message_type_counter.keys()),
        '# Trades': list(message_type_counter.values())
             }).sort('# Trades', descending=True)

    summary_path = os.path.join(DATA_DIR, 'summary')
    write_deltalake(summary_path, counter, mode='overwrite',storage_options={"allow_unsafe_rename": "true"})
    summary = pl.read_delta(  summary_path )
    print(summary)

def get_bar_stats(trades, bar='time'):
    trades = trades.with_columns( (pl.col('price')*1e-4).alias('price'),
      pl.col('timestamp').cast(pl.Datetime('ns')).alias('timestamp') )\
       .filter( (pl.col('cross')==0) & (pl.col('timestamp').dt.time().is_between( datetime.time(9,30,0), datetime.time(16,0,0) ) ) )\
       .drop('cross').sort('timestamp')
    if isinstance(trades, pl.LazyFrame):
        trades = trades.collect() 
    if trades.schema['timestamp'] == pl.Int64:
        trades = trades.with_columns(pl.col('timestamp').cast(pl.Datetime('ns')))
    
    trades = trades.sort('timestamp') 
    if bar== 'time':
        return trades.with_columns(
           pl.col('timestamp').dt.truncate('1m').alias('timestamp')\
              ).group_by('timestamp').agg(
        ( (pl.col('price')*pl.col('shares')).sum()/(1e-8+pl.col('shares').sum()) ).alias('vwap'),
          pl.col('price').first().alias('open'),
          pl.col('price').last().alias('close'),
          pl.col('price').max().alias('high'),
          pl.col('price').min().alias('low'),
          pl.col('shares').sum().alias('volume'),
          pl.col('shares').count().alias('txn')
        ).sort('timestamp', descending=False)

    elif bar=='volume':
        v_per_min = trades.select('shares').sum().item()/( 6.5*60 )
        trades = trades.with_columns(pl.col('shares').cum_sum().alias('c_vol'))
        return trades.with_columns(
            (pl.col('c_vol')/v_per_min).round(0).cast(pl.Int64).alias('n_vb')
        ).group_by('n_vb').agg(

          pl.col('timestamp').last().alias('timestamp'),
        ( (pl.col('price')*pl.col('shares')).sum()/(1e-8+pl.col('shares').sum()) ).alias('vwap'),
          pl.col('price').first().alias('open'),
          pl.col('price').last().alias('close'),
          pl.col('price').max().alias('high'),
          pl.col('price').min().alias('low'),
          pl.col('shares').sum().alias('volume'),
          pl.col('shares').count().alias('txn'),
          pl.col('c_vol').last().alias('c_vol')
         ).sort(['n_vb','timestamp'], descending=False)
    elif bar=='dollar':
        d_per_min = (trades['price'] * trades['shares']).sum()/(6.5*60)
        trades = trades.with_columns(
            (pl.col('price')*pl.col('shares')).cum_sum().alias('c_value')
    )

        return trades.with_columns(
        (pl.col('c_value')/d_per_min).round(0).cast(pl.Int64).alias('n_db')
        ).group_by('n_db').agg(

          pl.col('timestamp').last().alias('timestamp'),
        ( (pl.col('price')*pl.col('shares')).sum()/(1e-8+pl.col('shares').sum()) ).alias('vwap'),
          pl.col('price').first().alias('open'),
          pl.col('price').last().alias('close'),
          pl.col('price').max().alias('high'),
          pl.col('price').min().alias('low'),
          pl.col('shares').sum().alias('volume'),
          pl.col('shares').count().alias('txn'),
          pl.col('c_value').last().alias('c_value')
       ).sort(['n_db','timestamp'], descending=False)
    else:
        return None






