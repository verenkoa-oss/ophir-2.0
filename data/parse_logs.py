import json

def parse_adsb_logs(log_lines):
    parsed_data = []

    for line in log_lines:
        parts = line.split(',')
        if len(parts) < 5:  # Assume at least 5 relevant parts are needed
            continue

        aircraft_data = {
            "hex_code": parts[0].strip(),
            "callsign": parts[1].strip(),
            "military": parts[2].strip().lower() == 'true',
            "country": parts[3].strip(),
            "position_available": parts[4].strip().lower() == 'true',
        }
        
        parsed_data.append(aircraft_data)

    return json.dumps(parsed_data, indent=4)

if __name__ == "__main__":
    log_file_path = 'path/to/your/log/file.log'  # Update with actual log file path
    
    with open(log_file_path, 'r') as file:
        log_lines = file.readlines()
        
    parsed_json = parse_adsb_logs(log_lines)
    
    with open('parsed_adsb_logs.json', 'w') as json_file:
        json_file.write(parsed_json)
