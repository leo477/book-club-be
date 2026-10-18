import os

# app.limiter builds its limiter at import time from REDIS_URL, and pytest imports this package
# before any app module. Force an empty URL (deliberately overriding a developer's shell env or
# .env) so the suite always uses in-memory rate-limit storage and never touches a real Redis.
os.environ["REDIS_URL"] = ""
