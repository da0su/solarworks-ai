from datetime import datetime

def main():
    command = input("CEO command > ").strip()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print("===================================")
    print("Solar Works - Command Bot")
    print(f"Received command: {command}")
    print(f"Executed at: {now}")
    print("Status: command received successfully.")
    print("===================================")

    with open("command_log.txt", "a", encoding="utf-8") as f:
        f.write(f"[{now}] {command}\n")

if __name__ == "__main__":
    main()