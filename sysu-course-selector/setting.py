# number of concurrent request threads of each target lesson
# avoid too large CONCURRENT_REQUEST, or it will hit the server too hard
# OUGHT TO be int and 1 <= CONCURRENT_REQUEST <= 10
CONCURRENT_REQUEST = 1

# after TIMEOUT, course_selector will drop current request and try again
# OUGHT TO be int and 2 <= TIMEOUT <= 60
TIMEOUT = 15

# time interval between 2 successful request
# avoid too small DELAY, or it will hit the server too hard
# OUGHT TO be int and 1 <= DELAY <= 60
DELAY = 5

# Proxy mode: 'system', 'http', 'socks5', or 'none'.
# 'system' reads the current Windows proxy automatically.  For a local Clash-like
# HTTP proxy, use PROXY_MODE = 'http' and set PROXY_PORT (often 7897).
PROXY_MODE = 'system'
PROXY_HOST = '127.0.0.1'
PROXY_PORT = 7897

# Legacy names kept for callers that still import the old configuration.
USE_SOCKS5_PROXY = False
SOCKS5_PROXY_PORT = 1080
