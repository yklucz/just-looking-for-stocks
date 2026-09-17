"""Explicit research universe and context mappings; no inferred memberships."""
INITIAL_SYMBOLS = ('AAPL', 'SPY', 'NVDA', 'TSLA')
EXPANDED_SYMBOLS = ('MSFT', 'AMZN', 'GOOGL', 'META', 'JPM', 'XOM', 'JNJ', 'WMT')
SYMBOLS = INITIAL_SYMBOLS + EXPANDED_SYMBOLS
CONTEXT_SYMBOLS = ('SPY', 'QQQ', 'XLK', 'XLY', 'XLC', 'XLF', 'XLE', 'XLV', 'XLP')
SECTOR_ETFS = {
    'AAPL': 'XLK', 'MSFT': 'XLK', 'NVDA': 'XLK', 'TSLA': 'XLY', 'AMZN': 'XLY',
    'GOOGL': 'XLC', 'META': 'XLC', 'JPM': 'XLF', 'XOM': 'XLE', 'JNJ': 'XLV',
    'SPY': 'SPY', 'WMT': 'XLP',
}
UNIVERSE_VERSION = 'research-universe-v1'
