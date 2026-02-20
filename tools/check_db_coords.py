import sqlite3
import os
DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'fuel_optimizer', 'db.sqlite3')
if not os.path.exists(DB):
    print('DB_NOT_FOUND:', DB)
    raise SystemExit(1)
conn = sqlite3.connect(DB)
c = conn.cursor()
try:
    c.execute("SELECT count(*) FROM routing_fuelstation")
    total = c.fetchone()[0]
except Exception as e:
    print('ERROR_QUERY_TOTAL:', e)
    raise
try:
    c.execute("SELECT count(*) FROM routing_fuelstation WHERE latitude IS NOT NULL AND longitude IS NOT NULL")
    with_coords = c.fetchone()[0]
except Exception as e:
    print('ERROR_QUERY_COORDS:', e)
    raise
print('TOTAL:', total)
print('WITH_COORDS:', with_coords)
conn.close()
