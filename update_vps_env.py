
import os

env_path = "/root/polymarket-bot/.env"

if not os.path.exists(env_path):
    print(f"{env_path} not found")
    exit(1)

with open(env_path, "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith("FUNDER_ADDRESS="):
        new_lines.append("FUNDER_ADDRESS=0x3a1F68Bf518823A506E6BE1b390899a872921337\n")
    else:
        new_lines.append(line)

with open(env_path, "w") as f:
    f.writelines(new_lines)

print("Updated FUNDER_ADDRESS in .env!")
