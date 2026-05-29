"""清除缓存"""
import sqlite3
import sys
import os

db_path = os.path.join(os.path.dirname(__file__), 'DB', 'ebay_cache.db')

def clear_ebay_cache(keyword=None):
    conn = sqlite3.connect(db_path)
    if keyword:
        deleted = conn.execute(
            "DELETE FROM ebay_search_cache WHERE keywords LIKE ?",
            (f"%{keyword}%",)
        ).rowcount
        print(f"已清除 eBay 缓存中包含 '{keyword}' 的记录 ({deleted} 条)")
    else:
        deleted = conn.execute("DELETE FROM ebay_search_cache").rowcount
        print(f"已清除所有 eBay 缓存 ({deleted} 条)")
    conn.commit()
    conn.close()

def clear_sellersprite_cache(keyword=None):
    conn = sqlite3.connect(db_path)
    if keyword:
        deleted = conn.execute(
            "DELETE FROM sellersprite_search_cache WHERE keyword LIKE ?",
            (f"%{keyword}%",)
        ).rowcount
        print(f"已清除卖家精灵缓存中包含 '{keyword}' 的记录 ({deleted} 条)")
    else:
        deleted = conn.execute("DELETE FROM sellersprite_search_cache").rowcount
        print(f"已清除所有卖家精灵缓存 ({deleted} 条)")
    conn.commit()
    conn.close()

if __name__ == '__main__':
    keyword = sys.argv[1] if len(sys.argv) > 1 else None
    clear_ebay_cache(keyword)
    clear_sellersprite_cache(keyword)
