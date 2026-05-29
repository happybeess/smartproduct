import sqlite3, json
conn = sqlite3.connect(r'd:\smartproduct\DB\ebay_cache.db')
r = conn.execute("SELECT data FROM oe_search_cache ORDER BY created_at DESC LIMIT 1").fetchone()
if r:
    d = json.loads(r[0])
    items = d.get('ebay',{}).get('items',[])
    print('ebay count:', len(items))
    for i in items[:5]:
        print(f'  price={i.get("price")}, total={i.get("total")}, title={i.get("title","")[:50]}')
    print('avg_price:', d.get('ebay',{}).get('avg_price'))
    az_items = d.get('amazon',{}).get('items',[])
    print('\namazon count:', len(az_items))
    for i in az_items[:3]:
        print(f'  price={i.get("price")}, total={i.get("total")}, title={i.get("title","")[:50]}')
else:
    print('no cache')
conn.close()
