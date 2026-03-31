import sqlite3
import re

# Connect to the SQLite database (or create it)
db_file = 'aircraft_metadata.db'
connection = sqlite3.connect(db_file)
cursor = connection.cursor()

# Create table for aircraft metadata
cursor.execute('''
CREATE TABLE IF NOT EXISTS aircraft (
    id INTEGER PRIMARY KEY,
    callsign TEXT,
    aircraft_type TEXT,
    registration TEXT,
    altitude INTEGER,
    latitude REAL,
    longitude REAL,
    is_shadow BOOLEAN
)''')

def parse_ads_b_log(log_line):
    # Simple regex to match ADS-B log entries
    pattern = re.compile(r'(?P<callsign>[A-Z0-9]+)\s+(?P<aircraft_type>\w+)\s+(?P<registration>[A-Z0-9]+)\s+(?P<altitude>\d+)\s+(?P<latitude>[\d.-]+)\s+(?P<longitude>[\d.-]+)')
    match = pattern.search(log_line)
    if match:
        return {
            'callsign': match.group('callsign'),
            'aircraft_type': match.group('aircraft_type'),
            'registration': match.group('registration'),
            'altitude': int(match.group('altitude')),
            'latitude': float(match.group('latitude')),
            'longitude': float(match.group('longitude')),
            'is_shadow': False
        }
    else:
        # Handle SHADOW entries (no GPS data)
        shadow_pattern = re.compile(r'SHADOW\s+(?P<callsign>[A-Z0-9]+)\s+(?P<aircraft_type>\w+)\s+(?P<registration>[A-Z0-9]+)')
        shadow_match = shadow_pattern.search(log_line)
        if shadow_match:
            return {
                'callsign': shadow_match.group('callsign'),
                'aircraft_type': shadow_match.group('aircraft_type'),
                'registration': shadow_match.group('registration'),
                'altitude': None,  # No altitude for SHADOW entries
                'latitude': None,
                'longitude': None,
                'is_shadow': True
            }
    return None

# Function to import logs into the database
def import_logs(log_file):
    with open(log_file, 'r') as f:
        for line in f:
            parsed_data = parse_ads_b_log(line)
            if parsed_data:
                cursor.execute('''
                INSERT INTO aircraft (callsign, aircraft_type, registration, altitude, latitude, longitude, is_shadow)
                VALUES (?, ?, ?, ?, ?, ?, ?)''', 
                (parsed_data['callsign'], parsed_data['aircraft_type'], parsed_data['registration'], 
                 parsed_data['altitude'], parsed_data['latitude'], parsed_data['longitude'], 
                 parsed_data['is_shadow']))
    connection.commit()

# Example usage (you can replace 'adsb_logs.txt' with the path to your ADS-B logs)
import_logs('adsb_logs.txt')

# Close the database connection
connection.close()