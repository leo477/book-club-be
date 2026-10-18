import os

# The rate limiter is built at import time; keep it on in-memory storage so tests never touch a real Redis.
os.environ["REDIS_URL"] = ""
