#!/usr/bin/env python3
import sqlite3

LOGS_DATA = [
    {"hex": "AE1465", "callsign": "MOOSE68", "type": "C17", "country": "🇺🇸", "mil": True, "pos": False},
    {"hex": "AE49C5", "callsign": "MOOSE82", "type": "C17", "country": "🇺🇸", "mil": True, "pos": False},
    {"hex": "AE4F15", "callsign": "RCH569", "type": "C17", "country": "🇺🇸", "mil": True, "pos": False},
    {"hex": "7448A3", "callsign": "RJA132", "type": "A321", "country": "🇯🇴", "mil": False, "pos": True},
    {"hex": "711469", "callsign": "KNE554", "type": "A320", "country": "🇰🇼", "mil": False, "pos": True},
    {"hex": "040077", "callsign": "ETH431", "type": "B788", "country": "🇪🇹", "mil": False, "pos": False},
    {"hex": "739262", "callsign": "4XBOB", "type": "C172", "country": "🇮🇱", "mil": False, "pos": True},
    {"hex": "000000", "callsign": "SHADOW-X", "type": "UNKN", "country": "❓", "mil": True, "pos": False},
]

def import_archive():
    conn = sqlite3.connect('db/ophir.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS aircraft_archive (
        hex TEXT UNIQUE, callsign TEXT, type TEXT, country TEXT, 
        military BOOLEAN, position_available BOOLEAN, hits INTEGER DEFAULT 1
    )''')
    for ac in LOGS_DATA:
        cursor.execute('INSERT OR IGNORE INTO aircraft_archive VALUES (?,?,?,?,?,?,1)',
            (ac['hex'], ac['callsign'], ac['type'], ac['country'], ac['mil'], ac['pos']))
    conn.commit()
    print(f'✅ Импортировано {len(LOGS_DATA)} самолётов')
    conn.close()

if __name__ == "__main__":
    import_archive()
