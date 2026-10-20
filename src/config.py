from pathlib import Path 
from urllib.parse import urljoin


# root directory
BASE_DIR=Path(__file__).resolve().parent.parent

HTTPS_URL = 'https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH/' 
SOURCE_FILE = '10302019.NASDAQ_ITCH50.gz'
URL = urljoin(HTTPS_URL, SOURCE_FILE)

DATA_DIR = BASE_DIR/'data'
RAW_DIR = DATA_DIR/'raw' # store itch data
ORDERBOOK_DIR = BASE_DIR/'order_book'
MESSAGE_TYPES_DIR = BASE_DIR/'message_types/message_types.xlsx'

EVENTS_CODES = {'O': 'Start of Messages',
         'S': 'Start of System Hours',
         'Q': 'Start of Market Hours',
         'M': 'End of Market Hours',
         'E': 'End of System Hours',
         'C': 'End of Messages'}

ENCODINGS = {'primary_market_maker': {'Y': 1, 'N': 0},
       'printable': {'Y': 1, 'N': 0},
       'buy_sell_indicator': {'B': 1, 'S': -1},
       'cross_type': {'O': 0, 'C': 1, 'H': 2},
       'imbalance_direction': {'B': 0, 'S': 1, 'N': 0, 'O': -1}}

FORMATS = {
    ('integer', 2): 'H',  # int of length 2 => format string 'H'
    ('integer', 4): 'I',
    ('integer', 6): '6s',  # int of length 6 => parse as string, convert later
    ('integer', 8): 'Q',
    ('alpha', 1): 's',
    ('alpha', 2): '2s',
    ('alpha', 4): '4s',
    ('alpha', 8): '8s',
    ('price_4', 4): 'I',
    ('price_8', 8): 'Q',
}

MESSAGE_DIR = BASE_DIR/'messages'

TRADES_DIR = BASE_DIR/'trades'

